"""The ``specto`` CLI, built dynamically with click from ``api-info.json``
(contracts section 14).

    specto canvas submit --document @canvas.json --label "added a rear gallery"
    specto canvas undo                                    # name "canvas.undo" -> group + verb
    specto --json canvas state                            # result dict as JSON
    specto describe view:gallery                          # dotless names are top level
    specto instances                                      # the running Specto apps (#187)
    specto message rasel "show the rear run"              # a request to instance rasel's agent
    specto open ~/runs/day3                               # launch the app on a project (#215)

Required string arguments become positionals; everything else is an option. Results
that are ``{job}`` are waited on unless ``--no-wait``. The project is the nearest
``.specto/`` (like git) unless ``--project``.

Inline JSON and shells (``QUOTING_HELP``, shown by ``specto --help``): a lone backslash
in a JSON string (``\\d`` after a shell collapsed ``\\\\d``) is read as a backslash;
PowerShell 5.1 strips unescaped inner double quotes before the process starts, which the
CLI detects and answers with the ``\\"`` form and ``@file.json``.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click

from specto_core import Project
from specto_core.detached import spawn_detached
from specto_core.errors import SpectoError
from specto_core.instances import (
    caller_labels,
    default_sender,
    find_instance,
    list_instances,
    project_key,
)
from specto_sdk import Client, load_api_info
from specto_sdk.endpoint import ENV_ENDPOINT, ENV_PROJECT, find_endpoint

from . import __version__
from .plugin import plugin_group
from .render import TAG_INFO, TAG_OK, TAG_WARN, render_error, render_result

ENV_API_INFO = "SPECTO_API_INFO"
ENV_CONNECTION = "SPECTO_CONNECTION"
EXIT_ERROR = 1
EXIT_USAGE = 2


# -- shared state --------------------------------------------------------------------------
@dataclass
class CliState:
    project: str | None = None
    endpoint: str | None = None
    origin: str = "user"
    connection: str | None = None
    json: bool = False
    wait: bool = True
    timeout: float | None = None
    client_factory: Any = None  # tests inject a Client factory
    _client: Client | None = field(default=None, repr=False)

    def client(self) -> Client:
        if self._client is None:
            if self.client_factory is not None:
                self._client = self.client_factory(self)
            else:
                self._client = Client.open(
                    self.project,
                    endpoint=self.endpoint,
                    origin=self.origin,
                    client="specto-cli",
                    connection=self.connection or default_connection(),
                )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


pass_state = click.make_pass_decorator(CliState, ensure=True)


def default_connection() -> str:
    """Every CLI process of one user on one host presents the same connection name."""
    import getpass
    import socket

    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return f"cli:{user}@{socket.gethostname()}"


# -- parameter types ------------------------------------------------------------------------
QUOTING_HELP = """\
Quoting inline JSON: Git Bash / sh: single quotes, '{"keys":"^\\\\d{5}_"}'; a lone
backslash (\\d) is accepted as a backslash, so double quotes also work. PowerShell 5.1
strips inner double quotes before the command sees them: escape each as \\" inside
single quotes, '{\\"keys\\":\\"^\\\\d{5}_\\"}', or use @file.json (PowerShell 7.3+
passes them as is). cmd: double quotes with \\" inside."""

_LONE_BACKSLASH = re.compile(r'\\(?!["\\/bfnrtu])')


def loads_lenient(text: str) -> Any:
    """``json.loads`` that also accepts a lone backslash (``\\d`` in a regex) as a literal
    backslash: a shell that collapsed ``\\\\`` to ``\\`` otherwise turns a valid spec into an
    invalid JSON escape."""
    try:
        return json.loads(text)
    except ValueError:
        fixed = _LONE_BACKSLASH.sub(r"\\\\", text)
        if fixed == text:
            raise
        return json.loads(fixed)


def _quotes_stripped(text: str) -> bool:
    """A JSON object/array whose double quotes a shell removed (PowerShell 5.1)."""
    return text[:1] in "{[" and '"' not in text and ":" in text


class JsonParam(click.ParamType):
    """A JSON value, ``@file.json``, or (for arrays of strings) ``a,b,c``."""

    name = "json"

    def __init__(self, schema: dict[str, Any]) -> None:
        self.schema = schema

    def convert(self, value: Any, param: Any, ctx: Any) -> Any:
        if not isinstance(value, str):
            return value
        text = value
        if text.startswith("@"):
            try:
                text = Path(text[1:]).read_text(encoding="utf-8")
            except OSError as e:
                self.fail(f"cannot read {text[1:]}: {e}", param, ctx)
        try:
            return loads_lenient(text)
        except ValueError:
            pass
        if _is_string_array(self.schema):
            return [s.strip() for s in text.split(",") if s.strip()]
        if _quotes_stripped(text):
            self.fail(
                f"not JSON: the shell removed the double quotes: {value!r}. PowerShell 5.1 "
                'does this; escape each inner quote as \\" inside single quotes, '
                "or pass @file.json",
                param,
                ctx,
            )
        self.fail(f"not JSON (or @file): {value!r}", param, ctx)


def _is_string_array(schema: dict[str, Any]) -> bool:
    for alt in _alts(schema):
        if (
            alt.get("type") == "array"
            and (alt.get("items") or {}).get("type", "string") == "string"
        ):
            return True
    return False


def _alts(schema: dict[str, Any]) -> list[dict[str, Any]]:
    if "anyOf" in schema:
        return [a for a in schema["anyOf"] if a.get("type") != "null"]
    return [schema]


def _param_type(schema: dict[str, Any], defs: dict[str, Any]) -> tuple[Any, bool]:
    """(click type, is_flag) for a property schema."""
    alts = _alts(schema)
    if len(alts) == 1:
        a = alts[0]
        if "$ref" in a:
            return JsonParam(schema), False
        if "enum" in a:
            return click.Choice([str(v) for v in a["enum"]]), False
        t = a.get("type")
        if t == "string":
            return click.STRING, False
        if t == "integer":
            return click.INT, False
        if t == "number":
            return click.FLOAT, False
        if t == "boolean":
            return click.BOOL, True
    return JsonParam(schema), False


def _opt_name(prop: str) -> str:
    return "--" + prop.replace("_", "-")


# -- building --------------------------------------------------------------------------------
def build_cli(api_info: dict[str, Any] | None = None) -> click.Group:
    api_info = api_info or _load_api_info()
    root = _make_root(api_info)
    groups: dict[str, click.Group] = {}
    for cmd in api_info["commands"]:
        if cmd.get("deprecated_since"):
            continue
        group_name, _, verb = cmd["name"].partition(".")
        command = _make_command(cmd, name=verb or group_name)
        if verb:
            g = groups.get(group_name)
            if g is None:
                g = click.Group(group_name, help=f"{group_name}.* commands")
                groups[group_name] = g
                root.add_command(g)
            g.add_command(command)
        else:
            root.add_command(command, name=group_name.replace("_", "-"))
    # the extras replace generated commands of the same name (``message`` takes an instance)
    extras = (
        init_cmd,
        status_cmd,
        watch_cmd,
        api_info_cmd,
        instances_cmd,
        message_cmd,
        open_cmd,
        setup_cli_cmd,
    )
    for extra in (*extras, plugin_group):
        root.add_command(extra)
    return root


def _load_api_info() -> dict[str, Any]:
    path = os.environ.get(ENV_API_INFO)
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return load_api_info()


def _make_root(api_info: dict[str, Any]) -> click.Group:
    @click.group(
        name="specto",
        help=f"Specto command line (api level {api_info.get('api_level')}). "
        "Talks to the running app of the nearest .specto/ project.\n\n" + QUOTING_HELP,
        context_settings={"help_option_names": ["-h", "--help"]},
    )
    @click.version_option(__version__, "-V", "--version", prog_name="specto")
    @click.option(
        "--project",
        "-p",
        envvar=ENV_PROJECT,
        type=click.Path(),
        help="Project dir (default: nearest .specto/).",
    )
    @click.option(
        "--endpoint", envvar=ENV_ENDPOINT, help="pipe://name, unix://path or tcp://host:port."
    )
    @click.option("--origin", default="user", show_default=True, help="Provenance for changes.")
    @click.option(
        "--connection",
        envvar=ENV_CONNECTION,
        help="Connection name shared by CLI processes (default cli:<user>@<host>).",
    )
    @click.option("--json", "as_json", is_flag=True, help="Print the result dict as JSON.")
    @click.option("--no-wait", is_flag=True, help="Do not block on {job} results.")
    @click.option("--timeout", type=float, default=None, help="Seconds to wait for a job.")
    @click.pass_context
    def root(ctx, project, endpoint, origin, connection, as_json, no_wait, timeout):
        st = ctx.ensure_object(CliState)
        st.project = project or st.project
        st.endpoint = endpoint or st.endpoint
        st.origin = origin
        st.connection = connection or st.connection
        st.json = as_json or st.json
        st.wait = not no_wait
        st.timeout = timeout
        ctx.call_on_close(st.close)

    return root


def _make_command(cmd: dict[str, Any], *, name: str) -> click.Command:
    schema = cmd.get("args_schema") or {}
    defs = schema.get("$defs", {})
    props: dict[str, Any] = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    params: list[click.Parameter] = []
    positionals: list[str] = []

    for prop, ps in props.items():
        ptype, is_flag = _param_type(ps, defs)
        desc = ps.get("description") or ""
        if prop in required and ptype is click.STRING and not is_flag:
            params.append(click.Argument([prop], required=False))
            positionals.append(prop)
            continue
        if is_flag:
            params.append(
                click.Option(
                    [f"{_opt_name(prop)}/--no-{prop.replace('_', '-')}"], default=None, help=desc
                )
            )
            continue
        params.append(
            click.Option(
                [_opt_name(prop)],
                type=ptype,
                default=None,
                required=False,
                help=desc
                + (
                    f" (default {ps['default']!r})"
                    if "default" in ps and ps["default"] not in (None, "")
                    else ""
                ),
                metavar=_metavar(ps, ptype),
            )
        )
    params.append(
        click.Option(["--json", "as_json"], is_flag=True, help="Print the result as JSON.")
    )

    flags = []
    if cmd.get("read_only"):
        flags.append("read-only")
    if cmd.get("destructive"):
        flags.append("destructive")
    if cmd.get("effects"):
        flags.append("effects: " + ", ".join(cmd["effects"]))
    help_text = (cmd.get("doc") or cmd["name"]) + (f"  [{'; '.join(flags)}]" if flags else "")
    if any(isinstance(p.type, JsonParam) for p in params):
        help_text += (
            "\n\nJSON|@FILE options take inline JSON or @file.json; "
            "`specto --help` has the per-shell quoting rules."
        )

    @pass_state
    @click.pass_context
    def callback(ctx: click.Context, st: CliState, **kw: Any) -> None:
        as_json = kw.pop("as_json", False) or st.json
        args: dict[str, Any] = {}
        for prop in props:
            v = kw.get(prop)
            if v is None:
                continue
            args[prop] = v
        for prop in positionals:
            if prop in required and prop not in args:
                _fail(
                    "invalid_args",
                    f"{cmd['name']}: {prop} is required",
                    fix=f"pass the {prop} argument",
                )
        run_command(st, cmd["name"], args, as_json=as_json)

    return click.Command(name, params=params, callback=callback, help=help_text)


def _metavar(ps: dict[str, Any], ptype: Any) -> str | None:
    if isinstance(ptype, JsonParam):
        return "JSON|@FILE" + (",LIST" if _is_string_array(ps) else "")
    return None


# -- running -------------------------------------------------------------------------------
def run_command(st: CliState, name: str, args: dict[str, Any], *, as_json: bool = False) -> None:
    try:
        client = st.client()
        result = client.call(name, args)
        if (
            st.wait
            and isinstance(result, dict)
            and isinstance(result.get("job"), str)
            and set(result) <= {"job"}
        ):
            result = _wait_job(client, result["job"], st.timeout, quiet=as_json)
    except SpectoError as e:
        _fail(e.code, e.message, fix=e.fix)
    emit(result, as_json=as_json, name=name)


def _wait_job(client: Client, job: str, timeout: float | None, *, quiet: bool) -> dict[str, Any]:
    def progress(info: dict[str, Any]) -> None:
        if not quiet:
            pct = round(info["progress"] * 100)
            click.echo(f"[..] {job} {pct:3d}% {info['message']}".rstrip(), err=True)

    info = client.wait_job(job, timeout=timeout, on_progress=progress)
    if info["state"] != "done":
        _fail("job", f"job {job} {info['state']}: {info.get('error') or ''}".rstrip())
    if not quiet:
        click.echo(f"{TAG_OK} {job} done", err=True)
    return {"job": info}


def emit(result: Any, *, as_json: bool, name: str = "") -> None:
    if as_json:
        click.echo(json.dumps(result, indent=1, ensure_ascii=False))
    else:
        text = render_result(result, name=name)
        if text:
            click.echo(text)


def _fail(code: str, message: str, fix: str | None = None, exit_code: int = EXIT_ERROR) -> None:
    click.echo(render_error(code, message, fix), err=True)
    sys.exit(exit_code)


# -- built-in extras ---------------------------------------------------------------------------
@click.command("init")
@click.argument("directory", type=click.Path(), default=".")
@click.option("--name", default=None, help="Project name (default: the folder name).")
@pass_state
def init_cmd(st: CliState, directory: str, name: str | None) -> None:
    """Create a .specto/ project in DIRECTORY (no running app needed)."""
    try:
        proj = Project.init(Path(directory), name)
    except SpectoError as e:
        _fail(e.code, e.message, fix=e.fix)
    if st.json:
        emit({"project": proj.id, "name": proj.name, "root": str(proj.root)}, as_json=True)
    else:
        click.echo(f"{TAG_OK} project {proj.name!r} ({proj.id}) at {proj.root}")


@click.command("status")
@pass_state
def status_cmd(st: CliState) -> None:
    """Where the project is and whether its app answers."""
    out: dict[str, Any] = {}
    try:
        info, proj = find_endpoint(st.project, endpoint=st.endpoint)
    except SpectoError as e:
        if st.json:
            emit({"error": e.to_dict()}, as_json=True)
            sys.exit(EXIT_ERROR)
        _fail(e.code, e.message, fix=e.fix)
    out["project"] = {"id": proj.id, "name": proj.name, "root": str(proj.root)} if proj else None
    out["endpoint"] = info.model_dump()
    try:
        client = st.client()
        out["hello"] = client.hello_result
        out["canvas"] = client.call("canvas.state", {})["undo"]
    except SpectoError as e:
        out["error"] = e.to_dict()
    if st.json:
        emit(out, as_json=True)
        sys.exit(EXIT_ERROR if "error" in out else 0)
    if proj:
        click.echo(f"{TAG_INFO} project {proj.name} ({proj.id}) at {proj.root}")
    click.echo(f"{TAG_INFO} endpoint {info.url()} pid {info.pid}")
    if "error" in out:
        err = out["error"]
        click.echo(render_error(err["code"], err["message"], err.get("fix")), err=True)
        sys.exit(EXIT_ERROR)
    hello = out["hello"] or {}
    click.echo(
        f"{TAG_OK} {hello.get('server')} api level {hello.get('api_level')} "
        f"connection {hello.get('connection')}"
    )
    undo = out["canvas"]
    click.echo(f"canvas: undo {undo['cursor']}/{undo['depth']}")


@click.command("watch")
@click.option(
    "--since", type=int, default=None, help="Replay events after this seq (default: now)."
)
@click.option("--limit", type=int, default=None, help="Stop after this many events.")
@pass_state
def watch_cmd(st: CliState, since: int | None, limit: int | None) -> None:
    """Stream the canvas's events as JSON lines until interrupted."""
    try:
        client = st.client()
        if since is None:
            since = client.ping()["seq"]
        client.watch(since_seq=since)
        n = 0
        while limit is None or n < limit:
            ev = client.next_event(timeout=0.25)
            if ev is None:
                continue
            click.echo(json.dumps(ev, ensure_ascii=False))
            n += 1
    except SpectoError as e:
        _fail(e.code, e.message, fix=e.fix)
    except KeyboardInterrupt:
        click.echo(f"{TAG_WARN} interrupted", err=True)


@click.command("api-info")
@click.option("--live", is_flag=True, help="Ask the running app instead of the bundled copy.")
@pass_state
def api_info_cmd(st: CliState, live: bool) -> None:
    """Print api-info.json (set SPECTO_API_INFO=<file> to build the CLI from it)."""
    try:
        info = st.client().api_info() if live else _load_api_info()
    except SpectoError as e:
        _fail(e.code, e.message, fix=e.fix)
    emit(info, as_json=True)


@click.command("instances")
@pass_state
def instances_cmd(st: CliState) -> None:
    """The running Specto apps on this machine: name, project, pid (stale entries are
    swept). Address one with `specto message <name> <text>`."""
    live = list_instances()
    if st.json:
        emit([i.model_dump() for i in live], as_json=True)
        return
    if not live:
        click.echo(f"{TAG_INFO} no Specto instance is running")
        return
    for inst in live:
        click.echo(
            f"{inst.name}  {inst.project_name or Path(inst.project).name}  "
            f"{inst.project}  pid {inst.pid}"
        )


@click.command("message")
@click.argument("instance")
@click.argument("text", nargs=-1, required=True)
@click.option("--from", "sender", default=None, help="Sender (default: NIMITZ_ID, else user@host).")
@click.option("--reply-to", default=None, help="An id the sender wants quoted back.")
@pass_state
def message_cmd(
    st: CliState, instance: str, text: tuple[str, ...], sender: str | None, reply_to: str | None
) -> None:
    """Send TEXT to the agent of the running Specto INSTANCE (`specto instances` lists
    them). It shows in that agent's thread and runs as a turn labelled with the sender,
    unless the user set Peer requests to ask or off. This process's nimitz member labels
    go along: they verify the sender when they name it, else it shows as claimed."""
    try:
        inst = find_instance(instance)
        st.project, st.endpoint = inst.project, inst.url()
        args: dict[str, Any] = {"from": sender or default_sender(), "text": " ".join(text)}
        if reply_to:
            args["reply_to"] = reply_to
        labels = caller_labels()
        if labels:
            args["labels"] = labels
        result = st.client().call("message", args)
    except SpectoError as e:
        _fail(e.code, e.message, fix=e.fix)
    if st.json:
        emit(result, as_json=True)
    else:
        who = "verified" if result.get("verified") else "claimed, unverified"
        click.echo(
            f"{TAG_OK} {inst.name}: {result.get('status')} (peer requests: {result.get('mode')}; "
            f"sender {who})"
        )


# -- open (#215) -----------------------------------------------------------------------------
@click.command("open", context_settings={"ignore_unknown_options": True})
@click.argument("path", type=click.Path(), default=".")
@click.argument("app_args", nargs=-1, type=click.UNPROCESSED)
@click.option("--new", "new", is_flag=True, help="Start another window even if one is open.")
@click.option("--welcome", is_flag=True, help="Start on the welcome screen (PATH is ignored).")
@click.option("--list", "list_", is_flag=True, help="Print the recent projects and stop.")
@click.option("--log", "log", type=click.Path(), default=None, help="Append the app's output here.")
@click.option(
    "--wait",
    type=float,
    default=30.0,
    show_default=True,
    help="Seconds to wait for the instance to register (0: return right after the start).",
)
@click.option("--json", "as_json", is_flag=True, help="Print the result as JSON.")
@pass_state
def open_cmd(
    st: CliState,
    path: str,
    app_args: tuple[str, ...],
    new: bool,
    welcome: bool,
    list_: bool,
    log: str | None,
    wait: float,
    as_json: bool,
) -> None:
    """Open the project at PATH (default: the one the current folder is in) in the
    desktop app, detached from this terminal, and print its instance name and log.
    A folder that is in no project becomes one first (named after the folder, like
    `specto init`). If a window has that project open already it is brought to the
    front instead (unless --new). Anything after PATH goes to specto-app (`--no-web`,
    `--offscreen`).

    The app is launched from the Specto checkout's venv: SPECTO_APP_HOME, else what
    `specto setup-cli` kept, else the checkout this CLI is installed from."""
    from . import open as opener

    st.json = st.json or as_json
    if path.startswith("-"):  # `specto open --no-web`: an app flag, PATH defaulted
        app_args, path = (path, *app_args), "."
    if list_:
        _print_recents(st)
        return
    try:
        home = opener.resolve_app_home()
        root, created = (None, False) if welcome else _project_root_for_open(path)
        if created and not st.json:
            click.echo(f"{TAG_OK} initialised project {root.name!r} at {root}")
        before = opener.instances_of(root) if root is not None else []
        if before and not new:
            _raise_instance(st, before[0])
            return
        log_path = Path(log).expanduser().resolve() if log else opener.default_log_path()
        argv = opener.launch_argv(home, root, list(app_args))
        pid = spawn_detached(argv, log_path=log_path, cwd=root, env=opener.child_env())
    except SpectoError as e:
        _fail(e.code, e.message, fix=e.fix)
    except OSError as e:
        _fail("launch", f"cannot start the app: {e}", fix=f"check {home.python} runs")
    out: dict[str, Any] = {
        "pid": pid,
        "log": str(log_path),
        "app_home": str(home.root),
        "project": str(root) if root else None,
        "created": created,
        "instance": None,
    }
    if root is None:
        if not st.json:
            click.echo(f"{TAG_OK} welcome screen starting (pid {pid}); log {log_path}")
        else:
            emit(out, as_json=True)
        return
    inst = opener.wait_for_instance(root, pid, wait, before=before) if wait > 0 else None
    if inst is not None:
        # the registered pid is the interpreter's (a venv's python.exe is a launcher)
        out["instance"], out["instance_pid"] = inst.name, inst.pid
        if st.json:
            emit(out, as_json=True)
        else:
            click.echo(f"{TAG_OK} {inst.name}  {inst.project_name}  {root}  pid {inst.pid}")
            click.echo(f"{TAG_INFO} log {log_path}")
        return
    if wait > 0 and not opener.pid_alive(pid):
        tail = opener.log_tail(log_path)
        _fail(
            "launch",
            f"the app exited before registering (pid {pid}); its log ends:\n{tail}",
            fix=f"see {log_path}",
        )
    if st.json:
        emit(out, as_json=True)
        return
    why = "not waited for" if wait <= 0 else f"not registered after {wait:g}s"
    click.echo(f"{TAG_INFO} app starting on {root} (pid {pid}, {why}); log {log_path}")
    click.echo(f"{TAG_INFO} `specto instances` lists it once it is up")


def _project_root_for_open(path: str) -> tuple[Path, bool]:
    """The project root PATH names (PATH itself or the nearest .specto/ above it) and
    whether it was just made: a folder in no project becomes one, named after the
    folder (#217, as the app's open does). A path that is no folder is an error."""
    folder = Path(path).expanduser().resolve()
    if not folder.is_dir():
        raise SpectoError(f"{folder} is not a folder", fix="pass a folder; it becomes a project")
    proj = Project.find(folder)
    if proj is not None:
        return proj.root, False
    return Project.init(folder).root, True


def _raise_instance(st: CliState, inst: Any) -> None:
    """A window has the project open: bring it to the front over its endpoint."""
    from . import open as opener

    opener.allow_foreground(inst.pid)
    st.project, st.endpoint = inst.project, inst.url()
    result: dict[str, Any] = {"raised": False}
    try:
        result = st.client().call("window.raise", {})
    except SpectoError as e:
        if st.json:
            out = {"instance": inst.name, "pid": inst.pid, "project": inst.project}
            emit({**out, "raised": False, "error": e.to_dict()}, as_json=True)
            return
        click.echo(f"{TAG_WARN} {inst.name} did not raise its window: {e.message}", err=True)
    if st.json:
        out = {"instance": inst.name, "pid": inst.pid, "project": inst.project}
        emit({**out, "raised": bool(result.get("raised"))}, as_json=True)
        return
    name = inst.project_name or Path(inst.project).name
    click.echo(
        f"{TAG_OK} {name} is open already as {inst.name} (pid {inst.pid}); "
        + ("its window is in front" if result.get("raised") else "use --new for another window")
    )


def _print_recents(st: CliState) -> None:
    from specto_core.recents import load_recents

    entries = load_recents()
    if st.json:
        emit(entries, as_json=True)
        return
    if not entries:
        click.echo(f"{TAG_INFO} no recent projects")
        return
    open_on = {project_key(i.project): i.name for i in list_instances()}
    for e in entries:
        state = "missing" if e["missing"] else open_on.get(project_key(e["root"]), "")
        click.echo(f"{e['name']:<20} {e['root']}" + (f"  [{state}]" if state else ""))


@click.command("setup-cli")
@click.argument("checkout", type=click.Path(), default=None, required=False)
@click.option("--show", is_flag=True, help="Only report where the app would launch from.")
@click.option("--json", "as_json", is_flag=True, help="Print the result as JSON.")
@pass_state
def setup_cli_cmd(st: CliState, checkout: str | None, show: bool, as_json: bool) -> None:
    """Tell a globally installed `specto` which Specto checkout runs the app for
    `specto open`: CHECKOUT (default: the one this CLI is installed from) is kept in
    the user dir's cli.json. --show reports the current resolution instead."""
    from . import open as opener

    st.json = st.json or as_json

    if show or (checkout is None and not opener.checkout_of()):
        _show_app_home(st, opener)
        if show:
            return
        _fail(
            "no_checkout",
            "this install is not inside a Specto checkout",
            fix="pass the checkout: `specto setup-cli <folder>`",
        )
    root = Path(checkout).expanduser().resolve() if checkout else opener.checkout_of()
    problem = opener.home_problem(root)
    if problem:
        _fail("bad_checkout", problem, fix="pass the folder that holds packages/specto-app")
    path = opener.save_app_home(root)
    if st.json:
        emit({"app_home": str(root), "settings": str(path)}, as_json=True)
    else:
        click.echo(f"{TAG_OK} specto open launches from {root} (kept in {path})")


def _show_app_home(st: CliState, opener: Any) -> None:
    rows = [
        {"source": src, "path": str(p), "problem": opener.home_problem(p)}
        for src, p in opener.candidates()
    ]
    if st.json:
        emit(rows, as_json=True)
        return
    if not rows:
        click.echo(f"{TAG_WARN} no app home: {opener.SETUP_FIX}")
    for r in rows:
        tag = TAG_WARN if r["problem"] else TAG_OK
        why = f"  ({r['problem']})" if r["problem"] else ""
        click.echo(f"{tag} {r['source']:<9} {r['path']}{why}")


# -- entry point -----------------------------------------------------------------------------
cli = build_cli()


def main(argv: list[str] | None = None) -> int:
    try:
        cli.main(args=argv, prog_name="specto", standalone_mode=False)
    except click.ClickException as e:
        e.show()
        return e.exit_code
    except click.Abort:
        click.echo(f"{TAG_WARN} aborted", err=True)
        return EXIT_ERROR
    except SystemExit as e:
        return int(e.code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["CliState", "build_cli", "cli", "main", "run_command"]

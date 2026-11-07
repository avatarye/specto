"""The Claude Code session behind the prompt bar (ADR 0015, research #30): one
``ClaudeSDKClient`` per window, auto permission mode, the six Specto tools, the skill
tools (#161) and the command and data tools (#219) as an in-process SDK MCP server, and
the project's own ``.claude/`` and ``.specto/`` content; nothing else; a skill or
command added mid-session loads by reconnecting with resume after the turn. Personal
use on the user's own claude.ai login.

Guards (the acceptance criteria of #44):

- ``scrub_environment`` drops an inherited ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN``
  before the CLI starts, because the SDK merges Specto's environment into the CLI's and
  a key there silently outranks the login. ``SPECTO_AGENT_KEEP_API_KEY=1`` keeps them.
- ``claude_md_excludes`` lists every ancestor memory file (``~/.claude/CLAUDE.md`` among
  them when the data folder sits under home) for ``claudeMdExcludes``; with
  ``setting_sources=["project"]`` the user's own settings stay out too.
- The prompt bar lets a leading ``/`` through only for the project's own commands and
  skills (``slash_command``): the CLI dispatches any built-in otherwise.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from specto_core.agent_tools import AGENT_TOOLS
from specto_core.errors import SpectoError

from ..launchers.agent import LAUNCH_TOOLS
from ..walk.tools import WALK_TOOLS
from . import procs
from .commands import COMMAND_TOOLS
from .data import DATA_TOOLS
from .files import FILE_TOOLS
from .fleet import FLEET_TOOLS
from .link_code import LINK_CODE_TOOLS
from .scratch import SCRATCH_TOOLS
from .skills import SKILL_TOOLS, skills_key
from .turn import DeltaFn, TurnOutcome, TurnTools

SERVER_NAME = "specto"
#: the prompt bar's model chip (#73): Claude Code's aliases, so each names the current
#: generation; the CLI resolves them (``info["model"]`` shows the resolved id)
MODELS: tuple[str, ...] = ("opus", "sonnet", "haiku")
DEFAULT_MODEL = "sonnet"  # the user's choice (#73): fast enough, half the cost of opus
MODEL_LABELS = {"opus": "Opus", "sonnet": "Sonnet", "haiku": "Haiku"}
MODEL_DETAILS = {
    "opus": "most capable, slowest, about 2x the cost",
    "sonnet": "the default: fast, half the cost of opus",
    "haiku": "fastest, cheapest",
}
KEEP_KEY_VAR = "SPECTO_AGENT_KEEP_API_KEY"
#: credentials that outrank the claude.ai login when inherited (authentication precedence)
CREDENTIAL_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
#: a parent Claude Code session's markers: never meaningful for the window's own session
SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
#: the session's CLI: a path, or "bundled" for the Agent SDK's own; unset, the system's
#: ``claude`` on PATH when there is one (the build the user signs in with), else bundled
CLI_VAR = "SPECTO_AGENT_CLI"
#: a build to prefer over PATH's ``claude`` when it is installed (a path): a GUI launch's
#: PATH can find a public build first, which signs in with an Anthropic login, where the
#: build the user actually signs in with carries the machine's provider (401 otherwise).
#: Unlike ``CLI_VAR`` it is a preference: missing or not executable, PATH decides
PREFERRED_CLI_VAR = "SPECTO_CLAUDE_CLI"
#: the user settings that say how the system's Claude Code authenticates; the session
#: takes these and nothing else of the user's (ADR 0015: no user memory, hooks, plugins
#: or permissions)
AUTH_SETTINGS = ("apiKeyHelper", "awsCredentialExport", "awsAuthRefresh", "gcpAuthRefresh")
#: the user settings' env keys that choose the provider, its region and its models
AUTH_ENV_PREFIXES = ("CLAUDE_CODE_USE_", "ANTHROPIC_DEFAULT_")
AUTH_ENV = (
    "AWS_REGION",
    "AWS_PROFILE",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL_AWS_REGION",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "CLOUD_ML_REGION",
    "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
    "CLAUDE_CODE_SKIP_VERTEX_AUTH",
)
#: what the CLI's ``api_retry`` events carry when the provider refuses the credentials:
#: the CLI retries those with backoff for minutes, so the turn fails at the first one
AUTH_STATUSES = (401, 403)
AUTH_ERRORS = ("authentication_failed", "oauth_org_not_allowed")
MEMORY_FILES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude/CLAUDE.md")
#: Windows refuses a longer command line: CreateProcess fails with WinError 206, which
#: Python surfaces as FileNotFoundError and the Agent SDK relabels CLINotFoundError
#: (#199). The system prompt with the reference is far over it, so it rides in a file.
WINDOWS_COMMAND_LINE_MAX = 32767
WINERROR_COMMAND_LINE_TOO_LONG = 206

SYSTEM_PROMPT = """You are the agent of a Specto window: a desktop viewer showing one data folder \
(the project) on one canvas of views (file tree, gallery, preview, image, video, json, markdown, \
html, pdf, map, 3D, usd, scratch pad) connected by links. The user types short requests in the \
agent panel \
(it opens from the S icon at the window's bottom left); you compose the canvas for them by calling \
submit_canvas. Every message starts with the canvas \
context: the document (it is the state; or its hash when unchanged since your last turn, then the \
document you last saw or submitted is still current: do not call state), the user's viewport, the \
mouse position with the view under it, the current selections (selected items, selected files), \
and the project folder's files folder by folder (with many folders the deeper levels are folded \
into their parent: deeper says how many subfolders each hides); "this", "these" and "next to" \
refer to those.

Tools: state (the running document and undo state), describe (a view type, link kind, port type or \
'canvas', with schema and example), schema (the document's JSON Schema), submit_canvas (edits or a \
whole document; validate, diff, apply as one undo step; label = one line saying what changed), \
snap (check a view or the canvas), undo (one step), list_files (one folder of the project with its \
subfolders, or the files matching a glob: call it for a path the context folded away; never guess \
a path), launchers and launch (open files in an external program the user configured, or the \
system default; only when the user asks to open or edit files in a program). The reference at the \
end of this prompt lists \
every view type and link kind with its params, ports and examples: do not call describe or schema \
for them. A simple change (move, resize, relabel, add one view fed from the file tree, change a \
param) is one submit_canvas call with edits, composed from the context, nothing before it. edits \
names only what changes, in the document's own shape: {"views": {"kobi": {"layout": {"position": \
[330, 0]}}}} moves kobi; objects merge key by key, a list is replaced whole, null removes (a \
view's links go with it), and whatever edits leaves out is kept. Send the whole document only to \
rewrite most of the canvas. A submit that changes the canvas ends your turn: the user sees its \
label in the turn's row in the panel's thread, with an Undo button for the whole turn, so make the \
whole change in one submit and write no reply after it. A rejected submit lists every problem with \
a fix-it: fix them all and submit again without telling the user. If the user changed a view or \
link while you worked, a submit changing it is refused with its current entry: submit again on \
that. After three rejected submits in one turn, stop and say in one short line what you could not \
do.

Every view has a short name that is its id (kobi, tamu; the file tree has one too); the user sees \
it in the view's header and uses it to talk to you, so call views by name, never by type alone. \
The app hands out names: add a new view or link whole under any placeholder key (new1, new2) and \
the submit result's renamed tells you the name (views) or l<n> id (links) it got; names never \
change, so do not invent or rename them. A view's label is a brief summary of a few words ("front \
frames", "run 02 video"), shown after the name in the header.

Every view type has one role, given in the reference: a browser (file tree, gallery, scratch \
pad) shows many items of a collection and outputs a selection; a viewer (image, markdown, json, \
html, pdf, preview, urdf) shows the one selected item in its native form; a player (video, \
usd) is a \
viewer whose item has a timeline; a scene (3D, map) places many items in one space. Compose by \
role: a browser's selection feeds a viewer or player (a gallery picks the frame, an image view or \
the video shows it), rather than asking one view to both browse and show.

For a quick look ("show me this", "open that file", what the user picks in the file tree), use one \
preview: it renders the item by its kind (image, video, pdf, markdown, json, html, 3D model; other \
files as a card with name and size), is what Ctrl+double-click on a file opens, and shows \
"Contextual · follows selection" in its header. Reuse a preview already on the canvas rather than \
adding another. When the user wants a file kept open on its own ("keep the readme up"), use \
canvas.open_file with mode dedicated, what a plain double-click does: a view of the file's kind \
fed that one file by a pinned link, labelled "<name> · <folder>"; opening it again reveals the \
existing one. Add a typed view by hand (image, video, pdf, 3d, ...) only when a link needs its \
typed ports, such as a video's time for a time_map or a 3D camera.

For a USD world or stage (.usd, .usda, .usdc, .usdz), use one usd view: a player for one stage \
at a time, read-only. Feed it the file tree's picks with a selection intaker (source \
files.selected_files, screener ["@usd"]): it shows the last stage picked and steps through the \
rest. Its time is the stage's timecodes and follows the global clock: make it the root view \
when nothing else plays, or map a video's time onto it with a time_map. Params: renderer \
auto|rtx|storm (auto is rtx on Windows and Linux where the reference's machine.ovrtx is \
present, else storm, which runs on any GPU without the RTX look; on macOS auto is always \
storm; describe view:usd reports renderer_auto; one live rtx view per canvas), \
camera_speed, camera_mode (free, Unreal Editor's camera, or orbit, which turns around the \
selected prims; F frames their whole subtrees), look_through (a camera prim path of the \
stage, or null for the free camera), view_mode (lit|unlit|wireframe|collision; collision \
shows only the UsdPhysics colliders), environment (stage|sky|hdri: what lights the stage \
beyond its own lights; sky is one of NVIDIA's dynamic procedural skies, named by sky \
(ClearSky, Cirrus, CumulusLight, CumulusHeavy, Overcast, NightSky, CloudySky; fetched on \
first use), which only RTX renders, Storm shows the hdri for it; hdri is one of the 3D view's \
HDRIs, named by hdri; the choice is composed in the viewer, never written to the file), the \
sky's time_of_day (hours 0-24: the sun moves live), cloudiness and haze (0-1; null keeps the \
sky's own; the textured CloudySky has no haze), ground (true: the floor the skies ship with, \
at height 0) and max_pixels (the cap on rendered pixels; lower it to speed RTX up). Set its \
camera through the camera port (a follow link from a 3D view's camera shares one viewpoint) \
or through params, never by running code (no python link for it). snap on a usd view reports \
the stage path, the renderer running, the frame size, the camera, the camera mode, the \
environment that shows (with environment_note when it is not the choice, or the sky's fetch \
progress), the sky and hdri named and sky_controls in effect. Specto only views USD: never \
edit USD files, and say so when asked to change a stage.

For a robot description (.urdf), use one urdf view: a viewer for one robot at a time, \
read-only, never the root view, fed like the usd view (screener ["@urdf"]); a double-click on \
a .urdf opens one. Pose it through params.joints ({joint name: value}, radians and metres, \
clamped into the limits); never edit the URDF. Its meshes resolve through params.package_paths \
({package name: folder} or a list of folders), then the nearest package.xml, then \
ROS_PACKAGE_PATH and AMENT_PREFIX_PATH; a mesh not found draws as a placeholder and turns \
health warn, and snap lists it with its cause, so set package_paths when snap says a package \
was not found. Other params: view_mode (lit|unlit|wireframe|collision|visual_collision), up \
(z by default), environment, show_grid, show_frames, show_joint_axes, show_com, camera_speed, \
camera_mode. snap reports the robot, its links and joints, the pose and its source, the \
selected links, the missing meshes and the camera.

All files come from the project source: the fixed node "project" at the canvas origin, left of the \
file tree, with one output, project.all_files. Nobody moves, resizes, deletes or locks it, and \
every view must be reachable from it by links (a submit with a view that is not is refused with \
the link to add). Views fed from the file tree follow the user's selection by default: give their \
intaker scope "selection" (source files.selected_files; screener and keys apply to whatever the \
user picks, sources stay "." unless they narrow the picks), so changing the picks changes the \
view. Use scope "pinned" (source project.all_files, params.sources naming the data) only when the \
user asks for specific data by name ("always show the 02 run", "the front video"). Say which in \
the link summary ("shows the images you pick" / "always shows run 02"). describe link:intaker has \
one example of each. A view's intaker updates only when the user refreshes it unless its \
params.watch is true (auto-update, the switch in the view header): turn watch on for folders an \
agent writes into.

Anything an intaker, follow, read_contents or time_map link cannot express (a mapping between \
folders, a lookup across files, a join) is a per-pick Python link that you write and the user \
approves: write_link_code {name, code} writes .specto/links/<name>.py, then one submit adds the \
python link on it (resolves_to value: resolve(value, items, params, root) runs for each new value \
of the source port and returns the target port's value). Writing never approves: the code runs \
only once the user approved it in the view's header, and the panel shows the user the file with \
Open and Undo, so say nothing more about it. describe link:python gives the value and return \
shapes per port type and the worked example: the recording picked in the file tree \
(recordings/<session>/<clip>/...) selects the annotation folder \
(annotations/<session>/<clip>/geo) in a second file tree through its \
selection input, and a map shows that folder's GeoJSONs through a read_contents link. \
read_link_code shows a file's code and whether the user approved it.

A view can float: layout.floating ({corner, offset, by}) fixes it in the window above the \
pannable canvas (describe canvas, float). Float a view when the user wants it kept in sight \
while panning. A view the user floated (by: user) is theirs: you may unfloat it, never move \
or resize it. layout.collapsed: true shows a view as its title bar alone (its links keep \
flowing and it costs no quota); collapse views to make room rather than closing them.

The project has one scratch pad (view type scratch, at most one per canvas; it needs no link \
and the page it shows goes out on its items port): named pages (tabs) of markdown, each one file \
.specto/scratch/<page>.md, where the user drops and pastes what they find (text, pictures \
embedded from <page>.assets/, links to files relative to the page, quotes of items from views) \
and edits in place. The context's scratch names the pages with their size and headings. \
scratch_read gives a page's markdown; scratch_append {markdown and/or image or file, page} adds \
a block for the user (a finding, a picture, a file to come back to); scratch_write replaces a \
whole page (read it first and keep what the user wrote unless asked); scratch_pages, \
scratch_page_add, scratch_page_rename and scratch_page_remove change the pages. A page is named \
by id (p2) or name, and the active page (the one shown) is the default; the view's params.page \
chooses the page it shows and sends out. Each change is one undo step the user sees in the \
thread with Undo; say it in a few words. Change the pad only when the user asks for it.

Save a skill (save_skill: name, a one-line description of when to use it, the instructions) \
when the user teaches you a repeatable way of composing for this project: how they name things, \
which views suit which data, a link recipe they want again. Do not save one for a one-off \
request. It loads as specto:<name> from the next message: invoke it with the Skill tool when a \
request matches its description. Saving the same name updates it; delete_skill removes one. The \
strip shows the user the saved skill with Open and Undo, so write no reply about it beyond a \
few words.

Save a slash command (save_command: name, a one-line description, the body, an optional \
argument_hint) when the user wants to run something by name from the prompt bar: /<name> \
<arguments> sends you the body as their request, with $ARGUMENTS (or $1, $2) standing for what \
they typed after the name; a body may tell you to invoke one of your skills. A command carries \
no canvas context: call state first when its request needs the document. delete_command \
removes one. The panel shows the saved command with Open and Undo.

Data of your own lives under .specto/<folder>/<file> (json, md, txt or csv): data_write, \
data_read, data_list (the folders, or one folder's files) and data_delete; nothing else outside \
the canvas is yours to write, and the panel shows each write with Open and Undo. Build what a \
request needs from them and a skill, rather than asking for a tool: asked to remember a layout \
by name, write the views and links to keep (from the document, ids included) to \
.specto/layouts/<name>.json, and bring one back by reading it and submitting its views and \
links in one submit_canvas (one undo step; a python link restored this way keeps its code's \
approval state, which you never grant); save the way as a layouts skill and a /layout command \
the first time.

For "walk me through <view>" (or "how does <view> get its data"), give a walk-through: call \
walkthrough {view}; it shows the user that the walk is being prepared and returns the hops in the \
order they play, each with its link kind, summary, counts and a template caption. Then write every \
caption in one pass and call walkthrough_play once: {view, captions (one per hop, in hop order), \
intro, summary, chips, and speed (slow, normal, fast) only when the user asks for a slower or \
faster walk}. A caption is one or two short sentences on what the link does for every \
item and what arrives, names and numbers in **bold**; no item is followed. The walk only shows \
the canvas: never submit for it, and write no reply after walkthrough_play.

The user may run other Claude Code agents in nimitz fleets (paths such as FLEET, FLEET/SQUAD, \
FLEET/SQUAD/SHIP). fleet_status lists them; fleet_send messages one (it lands in its inbox as a \
request from this window, never as the user's word). Send only when the user asks you to tell or \
ask an agent something, or to answer a peer request (quote its reply_to); a refused send says \
why: tell the user in one line.

When you change nothing (a question, or a request you cannot do), reply in one or two short \
sentences, plain text. No markdown headings, no lists, no code unless asked. Do not describe your \
attempts."""


# -- guards ----------------------------------------------------------------------------------------
def scrub_environment(env: MutableMapping[str, str]) -> list[str]:
    """Remove what must not reach the session's CLI from ``env`` (Specto's own
    ``os.environ`` in the app: the SDK merges it into the subprocess environment, and
    the ``env`` option can only add). Returns the names removed, sorted."""
    removed: list[str] = []
    keep_key = env.get(KEEP_KEY_VAR, "") not in ("", "0")
    for name in CREDENTIAL_VARS:
        if name in env and not keep_key:
            removed.append(name)
    removed += [name for name in SESSION_VARS if name in env]
    for name in removed:
        env.pop(name, None)
    return sorted(removed)


def system_cli(environ: Mapping[str, str] | None = None) -> str | None:
    """The CLI the session runs: ``SPECTO_AGENT_CLI`` (a path; ``bundled`` for the Agent
    SDK's own), else ``SPECTO_CLAUDE_CLI`` when that build is installed, else the
    system's ``claude`` on PATH, so the window signs in as the user's own Claude Code
    does (a build with its own provider and credential exporter, say); None leaves it to
    the SDK, which runs its bundled CLI. On Windows only a native ``claude.exe`` counts:
    npm's ``claude.cmd`` shim cannot be spawned directly."""
    env = os.environ if environ is None else environ
    asked = env.get(CLI_VAR, "").strip()
    if asked:
        return None if asked == "bundled" else asked
    preferred = env.get(PREFERRED_CLI_VAR, "").strip()
    if preferred:
        known = Path(preferred)
        if known.is_file() and os.access(known, os.X_OK):
            return str(known)
    path = env.get("PATH")
    if not path:
        return None
    found = shutil.which("claude", path=path)
    if found and sys.platform == "win32" and not found.lower().endswith(".exe"):
        found = shutil.which("claude.exe", path=path)
    return found


def user_settings_path(environ: Mapping[str, str] | None = None) -> Path:
    """The user's Claude Code settings file: ``CLAUDE_CONFIG_DIR``/settings.json, else
    ``~/.claude/settings.json``."""
    env = os.environ if environ is None else environ
    base = env.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "settings.json"


def user_auth_settings(path: Path | None = None) -> dict[str, Any]:
    """What of the user's settings the session takes: ``AUTH_SETTINGS`` and the env keys
    that pick the provider, region and models (``AUTH_ENV``, ``AUTH_ENV_PREFIXES``).
    The session loads project settings only, so without these a Bedrock or Vertex user's
    window would fall back to whatever stale default credentials the machine has. A
    missing or unreadable file gives nothing."""
    try:
        user = json.loads((path or user_settings_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(user, dict):
        return {}
    out = {k: user[k] for k in AUTH_SETTINGS if isinstance(user.get(k), str) and user[k]}
    env = user.get("env") if isinstance(user.get("env"), dict) else {}
    keep = {
        k: str(v)
        for k, v in env.items()
        if k in AUTH_ENV or any(k.startswith(p) for p in AUTH_ENV_PREFIXES)
    }
    if keep:
        out["env"] = keep
    return out


def auth_message(what: str, cli: str | None = None) -> str:
    """The thread's message when the provider refuses the session's credentials."""
    runs = cli or "the Agent SDK's bundled CLI"
    return (
        f"the agent cannot sign in: the model provider refused its credentials ({what}; "
        f"CLI: {runs}); sign in again the way Claude Code on this machine does "
        f"(claude /login, or your cloud provider's sign-in), or set {CLI_VAR} to the claude "
        "you sign in with, then send the prompt again"
    )


def auth_failure(data: Mapping[str, Any], cli: str | None = None) -> str:
    """The thread's message for an ``api_retry`` event that is an authentication refusal
    (``AUTH_STATUSES``, ``AUTH_ERRORS``); ``""`` for any other retry (overload, a
    network blip), which the CLI's own backoff is right to wait out."""
    status = data.get("error_status")
    error = str(data.get("error") or "")
    if status not in AUTH_STATUSES and error not in AUTH_ERRORS:
        return ""
    return auth_message(" ".join(str(x) for x in (status, error) if x), cli)


#: a failed result the CLI did not retry (an invalid OAuth token: "Failed to
#: authenticate. API Error: 401 OAuth access token is invalid.")
AUTH_RESULT = re.compile(r"failed to authenticate|\b40[13]\b|oauth|authentication", re.I)


def auth_result(text: str, cli: str | None = None) -> str:
    """The thread's message for a failed result that is an authentication refusal;
    ``""`` for any other failure."""
    first = text.strip().splitlines()[0] if text.strip() else ""
    return auth_message(first[:200], cli) if AUTH_RESULT.search(first) else ""


def claude_md_excludes(root: Path) -> list[str]:
    """``claudeMdExcludes`` globs (absolute, forward slashes) for every memory file an
    ancestor of the project could contribute; the project's own files stay."""
    out: list[str] = []
    for parent in Path(root).resolve().parents:
        base = parent.as_posix().rstrip("/")
        out += [f"{base}/{name}" for name in MEMORY_FILES]
        out.append(f"{base}/.claude/rules/**")
    return out


def project_skills(root: Path) -> list[str]:
    """Skill names the session may use: ``<name>`` from ``.claude/skills``,
    ``specto:<name>`` from ``.specto/skills`` (the plugin namespace)."""
    names = [p.parent.name for p in sorted((root / ".claude" / "skills").glob("*/SKILL.md"))]
    names += [
        f"{SERVER_NAME}:{p.parent.name}"
        for p in sorted((root / ".specto" / "skills").glob("*/SKILL.md"))
    ]
    return sorted(names)


def project_commands(root: Path) -> list[str]:
    names = [p.stem for p in sorted((root / ".claude" / "commands").glob("*.md"))]
    specto = root / ".specto" / "commands"
    names += [f"{SERVER_NAME}:{p.stem}" for p in sorted(specto.glob("*.md"))]
    return sorted(names)


def slash_command(text: str, root: Path) -> str | None:
    """``None`` when the text is no slash command; the command's name when it is one of
    the project's own skills or commands; ``""`` when it is anything else (a Claude Code
    built-in such as ``/init``), which the prompt bar refuses."""
    text = text.lstrip()
    if not text.startswith("/"):
        return None
    name = text[1:].split(maxsplit=1)[0] if text[1:].strip() else ""
    allowed = set(project_skills(root)) | set(project_commands(root))
    return name if name in allowed else ""


def ensure_plugin(root: Path) -> Path | None:
    """``.specto/`` as a local plugin when it carries skills or commands: the manifest
    names it ``specto`` so its skills are ``specto:<name>``. Returns the plugin path."""
    specto = root / ".specto"
    if not ((specto / "skills").is_dir() or (specto / "commands").is_dir()):
        return None
    manifest = specto / ".claude-plugin" / "plugin.json"
    if not manifest.exists():
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {"name": SERVER_NAME, "description": "This project's Specto skills and commands"},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return specto


# -- the six tools as SDK tools ------------------------------------------------------------------
SESSION_FILE = "agent-session.json"
#: how long closing waits for the session to end (stdin EOF: the CLI saves the session
#: and exits) before the CLI is killed (#118)
CLOSE_S = 3.0
#: the executables the Agent SDK runs as the Claude CLI: its bundled ``claude`` binary, or
#: ``node`` for an npm install (#150: the session finds its CLI among its children)
CLI_NAMES = ("claude", "node")


def load_session_id(root: Path) -> str | None:
    """The window's last Claude Code session id, kept in ``.specto/`` so a relaunch
    resumes the conversation (spec #35 story 17)."""
    path = Path(root) / ".specto" / SESSION_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    sid = data.get("session_id") if isinstance(data, dict) else None
    return str(sid) if sid else None


def save_session_id(root: Path, session_id: str) -> None:
    path = Path(root) / ".specto" / SESSION_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"session_id": session_id}) + "\n", encoding="utf-8")


def sdk_tools(tools: TurnTools) -> list[Any]:
    """The six agent tools as in-process SDK MCP tools over ``TurnTools.call``, and the
    skill tools (#161), the command and data tools (#219), the link code tools (#168),
    ``list_files`` (#169), the walk-through tools (#174), the fleet tools (#187), the
    launcher tools (#186) and the scratch pad tools (#184) when the turn's tools have
    the project's stores, the window's walk or fleet, or the project's launchers for
    them."""
    from claude_agent_sdk import ToolAnnotations, tool

    specs = dict(AGENT_TOOLS)
    if tools.skills is not None:
        specs.update(SKILL_TOOLS)
    if tools.commands is not None:
        specs.update(COMMAND_TOOLS)
    if tools.data is not None:
        specs.update(DATA_TOOLS)
    if tools.files is not None:
        specs.update(FILE_TOOLS)
    if tools.link_code is not None:
        specs.update(LINK_CODE_TOOLS)
    if tools.walk is not None:
        specs.update(WALK_TOOLS)
    if tools.scratch is not None:
        specs.update(SCRATCH_TOOLS)
    if tools.fleet is not None:
        specs.update(FLEET_TOOLS)
    if tools.launch is not None:
        specs.update(LAUNCH_TOOLS)
    out = []
    for name, spec in specs.items():

        def make(name: str) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
            async def handler(args: dict[str, Any]) -> dict[str, Any]:
                res = await tools.acall(name, args)
                return {"content": [{"type": "text", "text": res.text}], "is_error": res.is_error}

            return handler

        annotations = ToolAnnotations(readOnlyHint=spec.read_only, destructiveHint=spec.destructive)
        out.append(tool(name, spec.doc, dict(spec.params), annotations)(make(name)))
    return out


def make_server(tools: TurnTools) -> Any:
    from claude_agent_sdk import create_sdk_mcp_server

    return create_sdk_mcp_server(SERVER_NAME, version="1.0.0", tools=sdk_tools(tools))


# -- options -----------------------------------------------------------------------------------
SUBMIT_TOOL = f"mcp__{SERVER_NAME}__submit_canvas"
#: playing a walk-through ends the turn too (#174)
WALK_PLAY_TOOL = f"mcp__{SERVER_NAME}__walkthrough_play"


def stop_hooks(done: Callable[[], bool]) -> dict[str, list[Any]]:
    """A PostToolUse hook on submit_canvas and walkthrough_play that stops the model
    once ``done()`` (the turn's tools: a submit changed the canvas, or a walk plays).
    The user's decision on #98: no closing reply after a change; the strip shows the
    submit's label. A rejected or empty submit
    lets the model go on (to fix it, or to answer)."""
    from claude_agent_sdk import HookMatcher

    async def after_submit(input_data: Any, tool_use_id: str | None, context: Any) -> Any:
        if not done():
            return {}
        return {"continue_": False, "stopReason": "the canvas changed; the strip shows the label"}

    matcher = f"{SUBMIT_TOOL}|{WALK_PLAY_TOOL}"
    return {"PostToolUse": [HookMatcher(matcher=matcher, hooks=[after_submit])]}


def system_prompt_text(reference: str = "") -> str:
    """The session's whole system prompt: ``SYSTEM_PROMPT``, then the reference."""
    return SYSTEM_PROMPT + ("\n\n" + reference if reference else "")


def prompt_file_path(root: Path) -> Path:
    """Where the session writes its system prompt for ``--system-prompt-file`` (#199):
    one file per project under the OS temp dir, outside the project (the data folder is
    the user's) and stable across reconnects."""
    digest = hashlib.sha1(Path(root).resolve().as_posix().encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / "specto" / "agent" / f"system-prompt-{digest}.md"


def agent_options(
    root: Path,
    server: Any,
    *,
    model: str | None = None,
    resume: str | None = None,
    reference: str = "",
    done: Callable[[], bool] | None = None,
    prompt_file: Path | None = None,
    cli_path: str | None = None,
    auth: dict[str, Any] | None = None,
) -> Any:
    """``ClaudeAgentOptions`` for one window's session: the Specto tools, the project's
    skills and commands, project settings only, auto permission mode, no auto memory,
    ancestor memory files excluded, partial messages for the strip; ``resume`` continues
    the session of the last launch; ``reference`` (``reference.reference_text``) ends
    the system prompt; ``done`` (``TurnTools.done``) stops the model after a submit that
    changed the canvas (``stop_hooks``). ``prompt_file`` (the session always gives one)
    takes the system prompt, which the CLI reads from it: on the command line it put
    the spawn over the Windows limit (#199). ``cli_path`` (``system_cli``) is the CLI to
    run, None for the SDK's bundled one; ``auth`` (``user_auth_settings``) joins the
    flag settings, so the session authenticates as the user's Claude Code does."""
    from claude_agent_sdk import ClaudeAgentOptions

    root = Path(root)
    skills = project_skills(root)
    plugin = ensure_plugin(root)
    prompt: Any = system_prompt_text(reference)
    if prompt_file is not None:
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(prompt, encoding="utf-8")
        prompt = {"type": "file", "path": str(prompt_file)}
    return ClaudeAgentOptions(
        system_prompt=prompt,
        tools=["Skill"] if skills else [],
        allowed_tools=[f"mcp__{SERVER_NAME}__*"],
        mcp_servers={SERVER_NAME: server},
        strict_mcp_config=True,
        permission_mode="auto",
        cwd=str(root),
        setting_sources=["project"],
        settings=json.dumps({**(auth or {}), "claudeMdExcludes": claude_md_excludes(root)}),
        cli_path=cli_path,
        env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
        plugins=[{"type": "local", "path": str(plugin)}] if plugin else [],
        skills=skills,
        include_partial_messages=True,
        model=model,
        resume=resume,
        hooks=stop_hooks(done) if done is not None else None,
    )


def start_error(e: Exception) -> Exception:
    """The error the thread shows when the session cannot start. The Agent SDK wraps
    whatever OSError the spawn raised: a FileNotFoundError becomes CLINotFoundError
    "Claude Code not found at <path>" even when the file exists (#199: Windows reports
    a command line over its limit as WinError 206, a FileNotFoundError). The real
    OSError, with its WinError, is the cause: name it, and for 206 say what it means.
    Any other error passes through."""
    cause = e.__cause__
    if not isinstance(cause, OSError):
        return e
    winerror = getattr(cause, "winerror", None)
    code = f"[WinError {winerror}] " if winerror else ""
    msg = (
        f"the agent did not start: {type(e).__name__}: {e}; the spawn failed with "
        f"{type(cause).__name__}: {code}{cause.strerror or cause}"
    )
    fix = None
    if winerror == WINERROR_COMMAND_LINE_TOO_LONG:
        msg += (
            f": the CLI's command line is over the Windows limit of "
            f"{WINDOWS_COMMAND_LINE_MAX} characters"
        )
        fix = "report it on avatarye/specto with the project path"
    return SpectoError(msg, fix=fix)


# -- the session -------------------------------------------------------------------------------
@dataclass
class _Job:
    prompt: str
    context_text: str
    on_delta: DeltaFn | None
    with_context: bool
    future: asyncio.Future[TurnOutcome] = field(
        default_factory=lambda: asyncio.get_event_loop().create_future()
    )


class ClaudeAgentSession:
    """One Claude Code session over the Agent SDK. The client lives in one worker
    coroutine (its task group must stay in the context that connected it); ``turn``
    queues a prompt and awaits the outcome."""

    def __init__(
        self,
        tools: TurnTools,
        root: Path,
        *,
        model: str | None = None,
        reference: str = "",
        environ: MutableMapping[str, str] | None = None,
        log: Callable[[str], None] = print,
    ) -> None:
        self.tools = tools
        self.reference = reference
        self.root = Path(root)
        #: the model the next turn runs on; ``applied_model`` is what the running CLI
        #: was started with or last switched to (``set_model`` at the next turn)
        self.model = model
        self.applied_model = model
        self.environ = os.environ if environ is None else environ
        self.log = log
        self.info: dict[str, Any] = {"model": model or "", "api_key_source": "", "session_id": ""}
        self.removed_env: list[str] = []
        self.stderr_tail: deque[str] = deque(maxlen=40)
        #: the session's running total as the CLI last reported it (ResultMessage
        #: total_cost_usd is cumulative over the session, resumed turns included)
        self.total_cost_usd = 0.0
        #: the session id of the last launch, resumed once; dropped when it will not start
        self.resume_id: str | None = load_session_id(self.root)
        #: the system prompt file the CLI reads (#199); written at each connect
        self.prompt_file: Path = prompt_file_path(self.root)
        self._queue: asyncio.Queue[_Job | None] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[None] | None = None
        self._client: Any = None
        self._busy = False  # a turn holds the worker (the queue is not read)
        self._cli_pids: set[int] = set()  # the CLIs this session started (#150)
        #: the CLI the session runs (``system_cli``; None: the SDK's bundled one)
        self.cli_path: str | None = system_cli(self.environ)

    async def start(self) -> None:
        if self._worker is not None and self._worker.done():
            # the CLI ended (crash, kill): the next prompt starts a fresh session
            self.log("[WARN] agent session ended; starting a new one")
            self._worker = None
            self._client = None
        if self._worker is not None:
            await self._ready  # type: ignore[misc]
            return
        loop = asyncio.get_running_loop()
        self._ready = loop.create_future()
        self._worker = loop.create_task(self._run_worker())
        await self._ready

    def options(self) -> Any:
        """The ``ClaudeAgentOptions`` the worker connects with (``agent_options`` for
        this session's project, model, resume id and reference; the system prompt
        written to ``prompt_file``)."""
        return agent_options(
            self.root,
            make_server(self.tools),
            model=self.model,
            resume=self.resume_id,
            reference=self.reference,
            done=lambda: self.tools.done,
            prompt_file=self.prompt_file,
            cli_path=self.cli_path,
            auth=user_auth_settings(user_settings_path(self.environ)),
        )

    async def _run_worker(self) -> None:
        from claude_agent_sdk import ClaudeSDKClient

        assert self._ready is not None
        pending: _Job | None = None
        try:
            self.removed_env = scrub_environment(self.environ)
            if self.removed_env:
                self.log(f"[WARN] agent session: ignoring inherited {', '.join(self.removed_env)}")
            while True:
                # the CLI fixes its skills allowlist and its commands at connect, so a
                # skill or command added, changed or removed loads by reconnecting with
                # resume: same conversation (#161, #219)
                loaded = skills_key(self.root)
                options = self.options()
                options.stderr = self.stderr_tail.append
                self.applied_model = self.model
                if self.resume_id:
                    self.log(f"[OK] agent session: resuming {self.resume_id}")
                before = procs.children(CLI_NAMES)
                async with ClaudeSDKClient(options) as client:
                    # the CLI the connect started: closing kills it if it ignores EOF (#150)
                    self._cli_pids |= procs.children(CLI_NAMES) - before
                    self._client = client
                    if not self._ready.done():
                        self._ready.set_result(None)
                    while True:
                        job, pending = pending or await self._queue.get(), None
                        if job is None:
                            return
                        if skills_key(self.root) != loaded:
                            pending = job  # changed while idle: load them first
                            break
                        self._busy = True
                        try:
                            job.future.set_result(await self._turn(client, job))
                        except Exception as e:
                            if not job.future.done():
                                job.future.set_exception(e)
                        finally:
                            self._busy = False
                        if skills_key(self.root) != loaded:
                            break  # the turn changed a skill: load it while the user reads
                self._client = None
                self.log(
                    "[OK] agent session: project skills or commands changed; reconnecting to "
                    "load them"
                )
        except Exception as e:
            if not self._ready.done():
                err = start_error(e)
                if self.resume_id and err is e:
                    # the old session is gone (or the CLI refused it): start fresh next time
                    self.log(f"[WARN] agent session {self.resume_id} did not resume: {e}")
                    self.resume_id = None
                else:
                    # the CLI never ran (#199): the conversation is intact, keep its id
                    self.log(f"[ERROR] agent session did not start: {err}")
                self._ready.set_exception(err)
            else:
                self.log(f"[ERROR] agent session ended: {type(e).__name__}: {e}")
        finally:
            self._client = None

    async def _turn(self, client: Any, job: _Job) -> TurnOutcome:
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            StreamEvent,
            SystemMessage,
            TextBlock,
        )

        if self.model != self.applied_model:
            # the chip changed since the last turn: switch the live session, no restart
            await client.set_model(self.model)
            self.applied_model = self.model
            self.log(f"[OK] agent session: model {self.model or 'default'} from this turn")
        if job.with_context:
            message = {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": job.context_text},
                        {"type": "text", "text": job.prompt},
                    ],
                },
                "parent_tool_use_id": None,
                "client_composed": True,
            }

            async def one() -> Any:
                yield message

            await client.query(one())
        else:
            await client.query(job.prompt)  # a project slash command: the CLI dispatches it
        streamed: list[str] = []
        blocks: list[str] = []
        #: output tokens per model call: the final usage of each message's stream
        call_tokens: list[int] = []
        outcome = TurnOutcome()
        t0 = time.perf_counter()
        first_token_ms = -1
        auth_error = ""
        async for m in client.receive_response():
            if isinstance(m, SystemMessage) and m.subtype == "api_retry":
                if not auth_error and (auth_error := auth_failure(m.data, self.cli_path)):
                    # the CLI would retry for minutes: stop it; its result ends the turn
                    self.log(f"[ERROR] agent session: {auth_error}")
                    await client.interrupt()
            elif isinstance(m, SystemMessage) and m.subtype == "init":
                d = m.data
                self.info.update(
                    model=d.get("model", ""),
                    api_key_source=d.get("apiKeySource", ""),
                    session_id=d.get("session_id", ""),
                    tools=d.get("tools", []),
                    skills=d.get("skills", []),
                    plugins=d.get("plugins", []),
                )
            elif isinstance(m, StreamEvent):
                if m.parent_tool_use_id is not None:
                    continue
                ev = m.event
                delta = ev.get("delta") or {}
                if first_token_ms < 0 and ev.get("type") in (
                    "content_block_start",
                    "content_block_delta",
                ):
                    first_token_ms = int((time.perf_counter() - t0) * 1000)
                if ev.get("type") == "message_delta" and "output_tokens" in (ev.get("usage") or {}):
                    call_tokens.append(int(ev["usage"]["output_tokens"]))
                if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    streamed.append(delta.get("text", ""))
                    if job.on_delta is not None:
                        job.on_delta(delta.get("text", ""))
            elif isinstance(m, AssistantMessage):
                if m.parent_tool_use_id is None:
                    blocks += [b.text for b in m.content if isinstance(b, TextBlock)]
            elif isinstance(m, ResultMessage):
                self.info["session_id"] = m.session_id
                if m.session_id and m.session_id != self.resume_id:
                    self.resume_id = m.session_id
                    save_session_id(self.root, m.session_id)
                cost = None
                if m.total_cost_usd is not None:
                    total = float(m.total_cost_usd)
                    # the turn's share: the total's growth (a smaller total: a new session)
                    cost = total - self.total_cost_usd if total >= self.total_cost_usd else total
                    self.total_cost_usd = total
                outcome = TurnOutcome(
                    text="".join(streamed) or "\n".join(blocks),
                    is_error=bool(m.is_error),
                    error=(m.result or "") if m.is_error else "; ".join(m.errors or []),
                    cost_usd=cost,
                    session_cost_usd=self.total_cost_usd if cost is not None else None,
                    session_id=m.session_id,
                    duration_ms=m.duration_ms,
                    api_ms=m.duration_api_ms,
                    first_token_ms=first_token_ms,
                    num_turns=m.num_turns,
                    output_tokens=int((m.usage or {}).get("output_tokens", -1)),
                    call_output_tokens=list(call_tokens),
                )
        if not auth_error and outcome.is_error:
            auth_error = auth_result(outcome.error or outcome.text, self.cli_path)
        if auth_error:
            outcome.is_error = True
            outcome.error = auth_error
            outcome.text = ""
        return outcome

    async def turn(
        self,
        prompt: str,
        context_text: str,
        *,
        on_delta: DeltaFn | None = None,
        with_context: bool = True,
    ) -> TurnOutcome:
        await self.start()
        worker = self._worker
        assert worker is not None
        job = _Job(prompt, context_text, on_delta, with_context)
        await self._queue.put(job)
        await asyncio.wait({job.future, worker}, return_when=asyncio.FIRST_COMPLETED)
        if not job.future.done():
            exc = worker.exception() if not worker.cancelled() else None
            why = f": {type(exc).__name__}: {exc}" if exc else ""
            raise RuntimeError(f"the agent session ended during the turn{why}")
        return job.future.result()

    def set_model(self, model: str | None) -> None:
        """The model for the next turn (the chip): the option when the CLI has not
        started yet, ``ClaudeSDKClient.set_model`` at the next turn when it has."""
        self.model = model
        self.info["model"] = model or self.info.get("model", "")

    async def interrupt(self) -> None:
        if self._client is not None:
            await self._client.interrupt()

    async def close(self) -> None:
        """End the session: the worker leaves the client, whose disconnect closes the
        CLI's stdin (it saves the session and exits); a turn still running is cancelled
        first, since it holds the worker. Bounded by ``CLOSE_S``: past it the worker is
        cancelled and a CLI still running is killed. A live CLI keeps pipe reads pending
        on the asyncio proactor, whose close then waits for them forever (#118)."""
        worker, self._worker = self._worker, None
        if worker is None:
            return
        if self._busy:
            worker.cancel()
        else:
            await self._queue.put(None)
        _, running = await asyncio.wait({worker}, timeout=CLOSE_S)
        if running:
            worker.cancel()
            await asyncio.wait({worker}, timeout=1.0)
        reap_cli(self._cli_pids)
        self._cli_pids = set()
        self.prompt_file.unlink(missing_ok=True)


def reap_cli(pids: Iterable[int] = ()) -> None:
    """Kill the Claude CLIs a session started that still run (``pids``), each with its
    process tree, now: a live CLI holds the loop's close, so the exit never comes (#118).
    First the Agent SDK's own exit-time reaper, a private name and so only a guarded fast
    path; then each of ``pids`` still a child of this process and still a CLI (#150), so
    a renamed or removed reaper cannot let a CLI that ignores EOF outlive the close."""
    try:
        from claude_agent_sdk._internal.transport import subprocess_cli
    except ImportError:
        subprocess_cli = None
    reap = getattr(subprocess_cli, "_kill_active_children", None)
    if callable(reap):
        reap()
    pids = set(pids)
    if pids:
        for pid in pids & procs.children(CLI_NAMES):
            procs.kill_tree(pid)


__all__ = [
    "DEFAULT_MODEL",
    "MODELS",
    "MODEL_DETAILS",
    "MODEL_LABELS",
    "SYSTEM_PROMPT",
    "ClaudeAgentSession",
    "agent_options",
    "auth_failure",
    "auth_message",
    "auth_result",
    "claude_md_excludes",
    "ensure_plugin",
    "load_session_id",
    "make_server",
    "project_commands",
    "project_skills",
    "prompt_file_path",
    "reap_cli",
    "save_session_id",
    "scrub_environment",
    "sdk_tools",
    "slash_command",
    "start_error",
    "stop_hooks",
    "system_cli",
    "system_prompt_text",
    "user_auth_settings",
    "user_settings_path",
]

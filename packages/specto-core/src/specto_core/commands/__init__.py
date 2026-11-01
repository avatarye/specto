"""Built-in command set. Importing this package registers every command into
``specto_core.registry.registry``: the canvas document commands behind the agent tools
(``submit``), discovery (``meta``) and jobs (``jobs_cmds``)."""

from . import jobs_cmds, meta, submit  # noqa: F401

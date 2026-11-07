"""Child process of test_app_close.py: ``SpectoApp.run()`` offscreen with an agent session
that, like the Claude CLI, is a child process the app reads from (a pipe read stays
pending on the proactor while the session lives). One prompt turn, then the window
closes the normal way. Prints ``AGENT <pid>`` once the session's process runs and
``RUN RETURNED`` when ``run()`` returns; the process should then exit.

    python app_close_child.py <project folder> [--mid-turn]
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QTimer

from specto_app.app import SpectoApp
from specto_app.prompt.turn import TurnOutcome
from specto_core import Project

#: the session's process: echoes each line back until stdin closes
ECHO = "import sys\nfor line in sys.stdin:\n    print(line.strip(), flush=True)\n"


class PipeSession:
    """An agent session over a child process, read and written through the asyncio
    loop's pipes as the Agent SDK does."""

    def __init__(self, hang: bool) -> None:
        self.hang = hang  # a turn that never ends (closing mid-turn)
        self.proc: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        if self.proc is None:
            self.proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", ECHO, stdin=subprocess.PIPE, stdout=subprocess.PIPE
            )
            print(f"AGENT {self.proc.pid}", flush=True)

    def set_model(self, model: str | None) -> None:
        pass

    async def turn(self, prompt, context_text, *, on_delta=None, with_context=True) -> TurnOutcome:
        await self.start()
        assert self.proc is not None and self.proc.stdin and self.proc.stdout
        if self.hang:
            print("TURN STARTED", flush=True)
            await asyncio.Event().wait()
        self.proc.stdin.write(b"hi\n")
        await self.proc.stdin.drain()
        line = await self.proc.stdout.readline()
        # the next read stays pending, as the CLI's stdout reader does between turns
        asyncio.get_running_loop().create_task(self.proc.stdout.readline())
        return TurnOutcome(text=line.decode().strip())

    async def interrupt(self) -> None:
        pass

    async def close(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            assert self.proc.stdin is not None
            self.proc.stdin.close()
            await self.proc.wait()


def main() -> int:
    project = Project.open_or_init(Path(sys.argv[1]))
    mid_turn = "--mid-turn" in sys.argv
    app = SpectoApp(
        project,
        jobs="inline",
        web=False,
        offscreen=True,
        serve=False,
        remember=False,
        agent=lambda tools: PipeSession(hang=mid_turn),
    )
    app.start()

    def close_window() -> None:
        print("CLOSING", flush=True)
        app.window.close()

    def ask() -> None:
        app.prompt_bar.submit("say hi")
        if mid_turn:
            QTimer.singleShot(500, close_window)

    if not mid_turn:
        app.prompt_bar.turnFinished.connect(lambda: QTimer.singleShot(100, close_window))
    QTimer.singleShot(200, ask)
    code = app.run()
    print("RUN RETURNED", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())

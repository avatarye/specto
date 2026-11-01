"""``close_agent()`` mutes ``aboutToQuit`` before it runs the nested loop that closes the
agent session: that loop's exit emits the signal a second time (Qt 6), reaching
receivers the first emission tore down (a segfault on Linux after any agent turn). Both
ways out of the main loop call ``close_agent()`` (``SpectoApp.run()`` and the welcome
launcher), so the mute is tested there, in a child process: it disconnects every
receiver of this process's application."""

from __future__ import annotations

import subprocess
import sys

CODE = """
from PySide6.QtCore import QCoreApplication
from specto_app.app import SpectoApp


class App:  # the close path alone, no shell: what the welcome launcher holds
    close_agent = SpectoApp.close_agent
    _mute_about_to_quit = SpectoApp._mute_about_to_quit

    def __init__(self, qapp):
        self.qapp, self._closing_bar, self.loop = qapp, None, None  # no session to close


qapp = QCoreApplication(["mute"])
hits = []
qapp.aboutToQuit.connect(lambda: hits.append("hit"))
qapp.aboutToQuit.emit()
before = len(hits)
App(qapp).close_agent()
qapp.aboutToQuit.emit()
print(before, len(hits))
"""


def test_close_agent_disconnects_about_to_quit_receivers():
    out = subprocess.run(
        [sys.executable, "-c", CODE], capture_output=True, text=True, check=True, timeout=120
    )
    assert out.stdout.split() == ["1", "1"], out.stdout + out.stderr

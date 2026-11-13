"""Copy today to server: pick the sessions (one per mouse), copy them, show progress.

Logic is in server_copy.py; this window only shows choices and polls the copy job.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, TypeVar

from PySide6 import QtCore, QtGui, QtWidgets

from ..server_copy import SUBFOLDER, CopyJob, SessionInfo, by_mouse, format_size

T = TypeVar("T")
CANCELLED = object()  # run_with_wait's result when the user pressed Cancel


def run_with_wait(parent: QtWidgets.QWidget, text: str, fn: Callable[[], T], show_after: float = 0.3) -> T | object:
    """Run ``fn`` on a worker thread; if it takes longer than ``show_after`` s, show a
    'please wait' box with Cancel so the window does not freeze (a lost network drive
    can block for tens of seconds). Returns fn's result, or ``CANCELLED``. Errors from fn
    are raised here. ``fn`` must only read files: it is abandoned, not stopped, on Cancel."""
    done = threading.Event()
    out: dict = {}

    def work():
        try:
            out["value"] = fn()
        except Exception as exc:
            out["error"] = exc
        finally:
            done.set()

    threading.Thread(target=work, name="server-lookup", daemon=True).start()
    if not done.wait(show_after):
        box = QtWidgets.QProgressDialog(text, "Cancel", 0, 0, parent)
        box.setWindowTitle("Please wait")
        box.setWindowModality(QtCore.Qt.WindowModal)
        box.setMinimumDuration(0)
        box.show()
        while not done.is_set() and not box.wasCanceled():
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 50)
            done.wait(0.02)
        cancelled = not done.is_set()
        box.close()
        box.deleteLater()
        if cancelled:
            return CANCELLED
    if "error" in out:
        raise out["error"]
    return out["value"]


class ServerCopyDialog(QtWidgets.QDialog):
    def __init__(self, parent, sessions: list[SessionInfo], server_dir: str | Path, data_dir: str | Path):
        super().__init__(parent)
        self.setWindowTitle("Copy today's sessions to the server")
        self.resize(760, 480)
        self.server_dir = Path(server_dir)
        self.job: CopyJob | None = None
        self._timer = QtCore.QTimer(self, interval=100, timeout=self._poll)
        lay = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            f"From <b>{data_dir}</b><br>to <b>{self.server_dir}/&lt;mouse&gt;/{SUBFOLDER}/</b><br>"
            "Files already on the server are skipped. Nothing on the server is changed or deleted.",
            wordWrap=True)
        lay.addWidget(intro)

        # one box per mouse: a tick box for a single session, a choice when there are several
        self._singles: list[tuple[QtWidgets.QCheckBox, SessionInfo]] = []
        self._choices: dict[str, tuple[QtWidgets.QButtonGroup, list[SessionInfo]]] = {}
        inner = QtWidgets.QWidget()
        boxes = QtWidgets.QVBoxLayout(inner)
        for mouse, items in by_mouse(sessions).items():
            box = QtWidgets.QGroupBox(mouse)
            b = QtWidgets.QVBoxLayout(box)
            if len(items) == 1:
                cb = QtWidgets.QCheckBox(items[0].summary(), checked=True)
                cb.setAccessibleName(f"Copy {items[0].name}")
                b.addWidget(cb)
                self._singles.append((cb, items[0]))
            else:
                b.addWidget(QtWidgets.QLabel(f"{len(items)} sessions today: pick the one to copy.", objectName="muted"))
                group = QtWidgets.QButtonGroup(box)
                for i, s in enumerate(items):
                    rb = QtWidgets.QRadioButton(s.summary())
                    rb.setAccessibleName(f"Copy {s.name}")
                    group.addButton(rb, i)
                    b.addWidget(rb)
                none = QtWidgets.QRadioButton("Don't copy this mouse")
                group.addButton(none, len(items))
                b.addWidget(none)
                group.idToggled.connect(self._update_buttons)
                self._choices[mouse] = (group, items)
            boxes.addWidget(box)
        boxes.addStretch()
        scroll = QtWidgets.QScrollArea(widgetResizable=True)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)
        for cb, _ in self._singles:
            cb.toggled.connect(self._update_buttons)

        self.progress = QtWidgets.QProgressBar(maximum=1000, textVisible=False)
        self.progress.setAccessibleName("Copy progress")
        lay.addWidget(self.progress)
        self.status = QtWidgets.QLabel("", wordWrap=True)
        lay.addWidget(self.status)
        self.problems = QtWidgets.QPlainTextEdit(readOnly=True, visible=False)
        self.problems.setAccessibleName("Copy problems")
        self.problems.setMaximumHeight(110)
        lay.addWidget(self.problems)

        row = QtWidgets.QHBoxLayout()
        row.addStretch()
        self.copy_btn = QtWidgets.QPushButton("Copy", objectName="primary")
        self.copy_btn.clicked.connect(self.start_copy)
        self.close_btn = QtWidgets.QPushButton("Close")
        self.close_btn.clicked.connect(self.reject)
        row.addWidget(self.copy_btn)
        row.addWidget(self.close_btn)
        lay.addLayout(row)
        self._update_buttons()

    # --- choices -------------------------------------------------------------------
    def undecided(self) -> list[str]:
        """Mice with several sessions where no choice was made yet."""
        return [m for m, (g, _) in self._choices.items() if g.checkedId() < 0]

    def chosen(self) -> list[SessionInfo]:
        out = [s for cb, s in self._singles if cb.isChecked()]
        for group, items in self._choices.values():
            i = group.checkedId()
            if 0 <= i < len(items):
                out.append(items[i])
        return sorted(out, key=lambda s: (s.mouse, s.time))

    def choose(self, mouse: str, index: int | None) -> None:
        """Pick session ``index`` of ``mouse`` (None: don't copy that mouse)."""
        group, items = self._choices[mouse]
        group.button(len(items) if index is None else index).setChecked(True)

    def _update_buttons(self, *_):
        if self.job is not None:
            return
        waiting = self.undecided()
        self.copy_btn.setEnabled(not waiting and bool(self.chosen()))
        if waiting:
            self.status.setText("Pick a session for: " + ", ".join(waiting))
        else:
            n = len(self.chosen())
            self.status.setText(f"{n} session{'s' if n != 1 else ''} to copy." if n else "Nothing selected.")

    # --- copy ----------------------------------------------------------------------
    def start_copy(self) -> None:
        if self.job is not None or self.undecided() or not self.chosen():
            return
        for w in self.findChildren(QtWidgets.QAbstractButton):
            if w is not self.close_btn:
                w.setEnabled(False)
        self.close_btn.setText("Cancel")
        self.job = CopyJob(self.chosen(), self.server_dir)
        self.job.start()
        self._timer.start()
        self._poll()

    def running(self) -> bool:
        return self.job is not None and not self.job.snapshot().finished

    def _poll(self) -> None:
        if self.job is None:
            return
        p = self.job.snapshot()
        if p.total_bytes:
            self.progress.setValue(int(1000 * p.done_bytes / p.total_bytes))
        if p.problems:
            self.problems.setVisible(True)
            self.problems.setPlainText("\n".join(p.problems))
        if not p.finished:
            self.status.setText(f"Copying {p.current}  ({format_size(p.done_bytes)} of {format_size(p.total_bytes)})")
            return
        self._timer.stop()
        self.close_btn.setText("Close")
        self.close_btn.setEnabled(True)
        if p.problems and not p.copied and not p.skipped:
            self.progress.setValue(0)
        elif not p.cancelled:
            self.progress.setValue(1000)
        text = f"{'Cancelled. ' if p.cancelled else 'Done. '}Copied {len(p.copied)} file(s)"
        if p.skipped:
            text += f", {len(p.skipped)} already on the server"
        if p.problems:
            text += f". {len(p.problems)} problem(s), see below"
        self.status.setText(text + ".")

    def _cancel(self) -> None:
        self.job.cancel()
        self.status.setText("Stopping after the current file…")
        self.close_btn.setEnabled(False)

    def reject(self) -> None:  # Close / Cancel button, Esc
        if self.running():
            self._cancel()
            return
        self._timer.stop()
        super().reject()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # window close button
        if self.running():
            self._cancel()
            event.ignore()
            return
        super().closeEvent(event)

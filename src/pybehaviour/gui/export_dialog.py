"""Settings ▸ Export settings as rig defaults… (preview, save to file, or write into the rig file)."""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtGui, QtWidgets

from ..rig_defaults import defaults_yaml, write_rig_defaults


class ExportDefaultsDialog(QtWidgets.QDialog):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.setWindowTitle("Export settings as rig defaults")
        self.main_window = main_window
        self.resize(780, 560)
        rig_file = main_window.rig_config.source
        self.rig_file = Path(rig_file) if rig_file else None
        lay = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            "The current settings as a <code>defaults:</code> block for a rig file. They become the "
            "starting values of this rig (presets override the task settings, not the rig settings: "
            "valve opening and noise gains). Setting names: docs/SETTINGS.md.",
            wordWrap=True)
        lay.addWidget(intro)
        self.changed_only = QtWidgets.QRadioButton("Only settings that differ from the built-in defaults (shorter)")
        self.all = QtWidgets.QRadioButton("All settings")
        self.changed_only.setChecked(True)
        for b in (self.changed_only, self.all):
            b.toggled.connect(self._refresh)
            lay.addWidget(b)
        self.preview = QtWidgets.QPlainTextEdit(readOnly=True)
        mono = QtGui.QFont()
        mono.setFamilies(["Consolas", "Menlo", "DejaVu Sans Mono", "Courier New", "monospace"])
        mono.setStyleHint(QtGui.QFont.TypeWriter)
        mono.setFixedPitch(True)
        self.preview.setFont(mono)
        self.preview.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)  # show it exactly as in the file
        self.preview.setAccessibleName("Defaults preview")
        lay.addWidget(self.preview, 1)
        note = QtWidgets.QLabel("Date, time, weights and the data folder (top-level behaviour_directory: in the rig file) are not exported. "
                              "Opto and PdCO window settings belong in presets.",
                                objectName="muted", wordWrap=True)
        lay.addWidget(note)

        row = QtWidgets.QHBoxLayout()
        copy = QtWidgets.QPushButton("Copy")
        copy.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.text()))
        self.save_btn = save = QtWidgets.QPushButton("Save to file…")
        save.clicked.connect(lambda: self.save_to_file())  # clicked sends a bool; don't take it as the path
        self.write_btn = QtWidgets.QPushButton(f"Write into {self.rig_file.name}" if self.rig_file else "Write into rig file")
        self.write_btn.setEnabled(self.rig_file is not None)
        self.write_btn.setToolTip(str(self.rig_file) if self.rig_file else "No rig file (simulation)")
        self.write_btn.clicked.connect(self.write_rig_file)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.reject)
        for b in (copy, save, self.write_btn):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(close)
        lay.addLayout(row)
        self.status = QtWidgets.QLabel("", wordWrap=True)
        lay.addWidget(self.status)
        self._refresh()

    def text(self) -> str:
        return defaults_yaml(self.main_window.params(), changed_only=self.changed_only.isChecked())

    def _refresh(self, *_):
        self.preview.setPlainText(self.text())

    def save_to_file(self, path: str | None = None) -> None:
        if path is None:
            start = str(self.rig_file.parent / "defaults.yaml") if self.rig_file else "defaults.yaml"
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save defaults", start, "YAML (*.yaml *.yml)")
            if not path:
                return
        Path(path).write_text("# Paste into a rig file (replaces its defaults: section)\n" + self.text(), encoding="utf-8")
        self.status.setText(f"Saved {path}")

    def write_rig_file(self) -> None:
        if self.rig_file is None:
            return
        answer = QtWidgets.QMessageBox.question(
            self, "Update rig file",
            f"Replace the defaults: section of\n{self.rig_file}\nwith these settings?\n\n"
            f"The rest of the file is kept, and a backup is saved as {self.rig_file.name}.bak.")
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            backup = write_rig_defaults(self.rig_file, self.main_window.params(), self.changed_only.isChecked())
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Rig file not changed", str(exc))
            return
        self.main_window.rig_defaults_changed()
        self.status.setText(f"Updated {self.rig_file.name} (backup: {backup.name}). These are now the rig defaults.")

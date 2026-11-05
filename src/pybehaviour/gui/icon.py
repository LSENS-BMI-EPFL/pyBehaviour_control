"""App icon (window title bar, taskbar, Alt+Tab).

The images in icons/ are made by scripts/make_icon.py from icons/drawing.png
(the MATLAB GUI's picture). pybehaviour.ico is for desktop / Start-menu shortcuts
and for the taskbar button.

On Windows a Python program's taskbar button normally shows python.exe's icon.
``use_own_taskbar_entry`` gives the process an app ID of its own, and
``tag_taskbar_window`` puts that ID, the .ico and a relaunch command on the main
window itself: the taskbar then shows this icon, and "Pin to taskbar" works.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtGui

ICONS = Path(__file__).resolve().parent / "icons"
ICO = ICONS / "pybehaviour.ico"
APP_ID = "LSENS.pyBehaviour"
DISPLAY_NAME = "LSENS behaviour control"


def app_icon() -> QtGui.QIcon:
    icon = QtGui.QIcon()
    for png in sorted(ICONS.glob("icon_*.png")):
        icon.addFile(str(png))
    return icon


def use_own_taskbar_entry() -> None:
    """Process-wide app ID. Call before the first window is created."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass  # cosmetic only


def relaunch_command() -> str:
    """How the taskbar starts the program again from a pinned button."""
    launcher = Path(sys.argv[0])
    for candidate in (launcher, launcher.with_suffix(".exe")):
        if candidate.name.lower().startswith("pybehaviour") and candidate.suffix.lower() == ".exe" and candidate.is_file():
            return f'"{candidate.resolve()}"'
    return f'"{sys.executable}" -m pybehaviour'


def tag_taskbar_window(widget) -> bool:
    """Set the taskbar properties (app ID, icon, name, relaunch command) on a shown top-level window.
    Returns True if they were set. Windows only; does nothing elsewhere."""
    if sys.platform != "win32":
        return False
    try:
        return _set_window_properties(int(widget.winId()), {
            5: APP_ID,                       # System.AppUserModel.ID
            2: relaunch_command(),           # .RelaunchCommand
            3: f"{ICO},0",                   # .RelaunchIconResource
            4: DISPLAY_NAME,                 # .RelaunchDisplayNameResource
        })
    except Exception:
        return False  # cosmetic only


def _set_window_properties(hwnd: int, values: dict[int, str]) -> bool:
    import ctypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", ctypes.c_ulong)]

    class PROPVARIANT(ctypes.Structure):  # just the VT_LPWSTR case
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                    ("value", ctypes.c_wchar_p), ("pad", ctypes.c_void_p)]

    iid_store = GUID(0x886D8EEB, 0x8CF2, 0x4446, (ctypes.c_ubyte * 8)(0x8D, 0x02, 0xCD, 0xBA, 0x1D, 0xBD, 0xCF, 0x99))
    appmodel = GUID(0x9F4C2855, 0x9F79, 0x4B39, (ctypes.c_ubyte * 8)(0xA8, 0xD0, 0xE1, 0xD4, 0x2D, 0xE1, 0xD5, 0xF3))
    store = ctypes.c_void_p()
    if ctypes.windll.shell32.SHGetPropertyStoreForWindow(ctypes.c_void_p(hwnd), ctypes.byref(iid_store),
                                                         ctypes.byref(store)) != 0:
        return False
    vtbl = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    set_value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY),
                                   ctypes.POINTER(PROPVARIANT))(vtbl[6])
    commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtbl[7])
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])
    ok = True
    try:
        for pid, text in values.items():  # SetValue copies the string
            ok &= set_value(store, ctypes.byref(PROPERTYKEY(appmodel, pid)), ctypes.byref(PROPVARIANT(31, 0, 0, 0, text))) == 0
        ok &= commit(store) == 0
    finally:
        release(store)
    return bool(ok)

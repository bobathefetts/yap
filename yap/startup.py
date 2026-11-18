"""Windows integration: 'Start with Windows' (per-user Run registry key) and a Start menu
shortcut. No admin rights needed.

Note: when Yap is launched by a packaged (Microsoft Store / MSIX) app, Windows silently
redirects registry and AppData writes into that app's private storage, where Windows itself
never looks. So these registrations are only made when Yap runs as a normal app.
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import sys
import winreg
from pathlib import Path

log = logging.getLogger("yap")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "Yap"
START_MENU_LNK = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Yap.lnk"
OLD_NAME = "Murmur"  # Yap's previous name
OLD_START_MENU_LNK = START_MENU_LNK.with_name("Murmur.lnk")


def _package_identity() -> str | None:
    try:
        k32 = ctypes.windll.kernel32
        length = ctypes.c_uint32(0)
        if k32.GetCurrentPackageFullName(ctypes.byref(length), None) == 15700:  # no package
            return None
        buf = ctypes.create_unicode_buffer(length.value + 1)
        return buf.value if k32.GetCurrentPackageFullName(ctypes.byref(length), buf) == 0 else "unknown"
    except Exception:
        return None


def _appdata_redirected_to() -> str | None:
    """Write a probe file to AppData and see whether Windows actually stored it inside some
    app package's private folder (happens to programs started by MSIX apps, even ones that
    report no package identity)."""
    appdata, local = os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")
    if not appdata or not local:
        return None
    probe = f"yap-probe-{os.getpid()}.tmp"
    path = Path(appdata) / probe
    try:
        path.write_text("probe")
        with os.scandir(Path(local) / "Packages") as it:
            for entry in it:
                if os.path.exists(os.path.join(entry.path, "LocalCache", "Roaming", probe)):
                    return entry.name
    except OSError:
        pass
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    return None


def package_name() -> str | None:
    """App package whose private storage our registry/AppData writes go to, or None when
    running as a normal app (writes land where Windows looks)."""
    return _package_identity() or _appdata_redirected_to()


def launch_command() -> str:
    if getattr(sys, "frozen", False):  # packaged Yap.exe
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    launcher = Path(__file__).resolve().parent.parent / "yap.pyw"
    return f'"{pythonw}" "{launcher}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            value, _ = winreg.QueryValueEx(k, NAME)
            return bool(value)
    except OSError:
        return False


def _current_value() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, NAME)[0]
    except OSError:
        return None


def set_enabled(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            if _current_value() != launch_command():
                winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(k, NAME)
            except FileNotFoundError:
                pass


def _shortcut_points_here() -> bool:
    try:
        data = START_MENU_LNK.read_bytes()
    except OSError:
        return False
    exe = sys.executable.lower()
    return exe.encode("mbcs", "ignore") in data.lower() or exe.encode("utf-16-le") in data.lower()


def ensure_start_menu_shortcut() -> None:
    """Create (or repoint) Start menu > Yap, for the packaged Yap.exe."""
    if not getattr(sys, "frozen", False) or _shortcut_points_here():
        return
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:YAP_LNK); "
              "$s.TargetPath = $env:YAP_EXE; $s.WorkingDirectory = $env:YAP_DIR; "
              "$s.IconLocation = $env:YAP_EXE + ',0'; $s.Description = 'Yap: local dictation'; $s.Save()")
    env = dict(os.environ, YAP_LNK=str(START_MENU_LNK), YAP_EXE=sys.executable,
               YAP_DIR=os.path.dirname(sys.executable))
    START_MENU_LNK.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env,
                   timeout=30, creationflags=0x08000000, capture_output=True)
    log.info("Start menu shortcut %s", "created" if START_MENU_LNK.exists() else "could not be created")


def _program_missing(command: str) -> bool:
    """True if the program a command line (or shortcut target) starts no longer exists."""
    m = re.match(r'\s*"([^"]+)"|\s*(\S+)', command or "")
    path = m and (m.group(1) or m.group(2))
    return bool(path) and not os.path.exists(path)


def remove_broken_old_entries() -> None:
    """Yap used to be called Murmur. Remove that name's startup entry and Start menu shortcut
    once the program they start is gone (an existing Murmur install is left alone)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as k:
            value = winreg.QueryValueEx(k, OLD_NAME)[0]
            if "murmur" in value.lower() and _program_missing(value):
                winreg.DeleteValue(k, OLD_NAME)
                log.info("Removed the old Murmur startup entry")
    except OSError:
        pass
    if OLD_START_MENU_LNK.exists():
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              "(New-Object -ComObject WScript.Shell).CreateShortcut($env:YAP_LNK).TargetPath"],
                             env=dict(os.environ, YAP_LNK=str(OLD_START_MENU_LNK)), timeout=30,
                             creationflags=0x08000000, capture_output=True, text=True).stdout.strip()
        if out and _program_missing(f'"{out}"'):
            OLD_START_MENU_LNK.unlink(missing_ok=True)
            log.info("Removed the old Murmur Start menu shortcut")


def apply(cfg: dict) -> None:
    """Called at every launch: keep the Start menu shortcut and startup entry correct."""
    pkg = package_name()
    if pkg:
        log.info("Launched from inside the app package %s: Windows would redirect the Start menu "
                 "and startup entries there, so they are skipped. Start Yap from File Explorer, "
                 "the Start menu, or at sign-in to register them.", pkg)
        return
    log.info("Running as a normal Windows app (%s)", sys.executable)
    try:
        remove_broken_old_entries()
        if cfg.get("start_menu_shortcut", True):
            ensure_start_menu_shortcut()
        want = cfg.get("start_with_windows")
        if want is not None:
            set_enabled(bool(want))
            log.info("Start with Windows: %s", "on" if want else "off")
    except Exception as exc:
        log.warning("Could not update Windows integration: %s", exc)

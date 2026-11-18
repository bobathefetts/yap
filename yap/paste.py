"""Puts text into whatever app has focus: clipboard + Ctrl+V, then restores your clipboard."""
from __future__ import annotations

import ctypes
import logging
import time

import pyperclip

from .hotkey import KEYEVENTF_KEYUP, YAP_TAG

log = logging.getLogger("yap")
user32 = ctypes.WinDLL("user32", use_last_error=True)

VK_CONTROL, VK_V = 0x11, 0x56
_MODIFIERS = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # shift, ctrl, alt, lwin, rwin


def _wait_for_modifiers_released(timeout: float = 2.0) -> None:
    # If Win/Alt/Shift are still physically held, Ctrl+V would become a different shortcut.
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in _MODIFIERS):
            return
        time.sleep(0.02)


def _send_ctrl_v() -> None:
    user32.keybd_event(VK_CONTROL, 0, 0, YAP_TAG)
    user32.keybd_event(VK_V, 0, 0, YAP_TAG)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, YAP_TAG)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, YAP_TAG)


def _clipboard_get() -> str | None:
    for _ in range(5):
        try:
            return pyperclip.paste()
        except Exception:
            time.sleep(0.05)
    return None


def _clipboard_set(text: str) -> bool:
    for _ in range(5):
        try:
            pyperclip.copy(text)
            return True
        except Exception:
            time.sleep(0.05)
    return False


def paste_text(text: str, restore_clipboard: bool = True) -> bool:
    if not text:
        return False
    _wait_for_modifiers_released()
    previous = _clipboard_get() if restore_clipboard else None
    if not _clipboard_set(text):
        log.error("Could not write to the clipboard")
        return False
    time.sleep(0.03)
    _send_ctrl_v()
    if restore_clipboard and previous:
        # Give the target app time to read the clipboard before putting the old text back.
        time.sleep(0.35)
        if _clipboard_get() == text:
            _clipboard_set(previous)
    return True

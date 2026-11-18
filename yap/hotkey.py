"""Global hold-to-talk hotkey (default Ctrl+Win) using a low-level keyboard hook."""
from __future__ import annotations

import ctypes
import logging
import threading
from typing import Callable

from pynput import keyboard

log = logging.getLogger("yap")

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
KEYEVENTF_KEYUP = 0x0002
# Tag for keystrokes Yap injects itself, so the hook can ignore them.
YAP_TAG = 0x59415021  # "YAP!"
# An unassigned virtual key. Tapping it while Win/Alt is held stops Windows from
# opening the Start menu (or an app's menu bar) when the key is released.
VK_MASK = 0xE8

_GROUPS: dict[str, set[int]] = {
    "ctrl": {0x11, 0xA2, 0xA3},
    "win": {0x5B, 0x5C},
    "alt": {0x12, 0xA4, 0xA5},
    "shift": {0x10, 0xA0, 0xA1},
    "space": {0x20},
    "caps_lock": {0x14},
    "scroll_lock": {0x91},
    "pause": {0x13},
    "insert": {0x2D},
    "menu": {0x5D},
    "right_ctrl": {0xA3},
    "right_alt": {0xA5},
}
_GROUPS.update({f"f{i}": {0x6F + i} for i in range(1, 25)})


def parse_hotkey(spec: str) -> list[set[int]]:
    groups = []
    for token in spec.lower().replace(" ", "").split("+"):
        if token in _GROUPS:
            groups.append(_GROUPS[token])
        elif len(token) == 1 and token.isalnum():
            groups.append({ord(token.upper())})
        else:
            raise ValueError(f"Unknown key {token!r} in hotkey {spec!r}")
    if not groups:
        raise ValueError("Empty hotkey")
    return groups


def tap_key(vk: int) -> None:
    user32.keybd_event(vk, 0, 0, YAP_TAG)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, YAP_TAG)


class HoldHotkey:
    """Calls on_start when every key in the combo is down, on_stop when one is released,
    and on_cancel if another key is pressed mid-hold (e.g. Ctrl+Win+Right to switch desktops)."""

    def __init__(self, spec: str, on_start: Callable[[], None], on_stop: Callable[[], None],
                 on_cancel: Callable[[], None]):
        self.groups = parse_hotkey(spec)
        self.combo_keys = set().union(*self.groups)
        self.needs_mask = bool(self.combo_keys & (_GROUPS["win"] | _GROUPS["alt"]))
        self.on_start, self.on_stop, self.on_cancel = on_start, on_stop, on_cancel
        self.down: set[int] = set()
        self.active = False
        self.cancelled = False  # stays True until the combo is fully released
        self._listener: keyboard.Listener | None = None
        self._lock = threading.Lock()

    # Called on the hook thread for every key event: keep it tiny and never block.
    def _filter(self, msg: int, data) -> bool:
        if data.dwExtraInfo == YAP_TAG:
            return False
        vk = data.vkCode
        with self._lock:
            if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
                if vk in self.down:
                    return False  # auto-repeat
                # Forget keys whose key-up we never saw (e.g. Win+L locks before the release).
                self.down = {k for k in self.down if self._physically_down(k)}
                self.down.add(vk)
                if self.active and vk not in self.combo_keys:
                    self.active, self.cancelled = False, True
                    self.on_cancel()
                elif not self.active and not self.cancelled and self._combo_held() \
                        and not (self.down - self.combo_keys):
                    self.active = True
                    if self.needs_mask:
                        tap_key(VK_MASK)
                    self.on_start()
            elif msg in (WM_KEYUP, WM_SYSKEYUP):
                self.down.discard(vk)
                if self.active and not self._combo_held():
                    self.active = False
                    self.on_stop()
                if not (self.down & self.combo_keys):
                    self.cancelled = False
        return False  # False = do not forward to pynput callbacks (the key still reaches apps)

    @staticmethod
    def _physically_down(vk: int) -> bool:
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)

    def _combo_held(self) -> bool:
        return all(self.down & g for g in self.groups)

    def start(self) -> None:
        self._listener = keyboard.Listener(win32_event_filter=self._filter)
        self._listener.daemon = True
        self._listener.start()
        log.info("Hotkey listener started")

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()

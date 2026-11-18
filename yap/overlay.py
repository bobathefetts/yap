"""Small floating pill near the bottom of the screen: mic level while recording,
pulsing dots while thinking. It never takes keyboard focus and ignores clicks,
so the text still pastes into the app you were typing in."""
from __future__ import annotations

import ctypes
import math
import time
import tkinter as tk
from collections import deque
from ctypes import wintypes
from typing import Callable

from PIL import Image, ImageDraw, ImageTk

user32 = ctypes.WinDLL("user32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x80000, 0x20
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_TOPMOST = 0x80, 0x8000000, 0x8
SWP_NOSIZE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x10, 0x40
HWND_TOPMOST = -1
KEY = "#010203"  # transparent colour key

BG, BORDER, FG, RED, AMBER = "#1d1d20", "#3c3c42", "#f2f2f2", "#ff453a", "#ffb340"


def _work_area() -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
    return rect.left, rect.top, rect.right, rect.bottom


class Overlay:
    BARS = 11

    def __init__(self, root: tk.Tk, state_fn: Callable[[], tuple[str, float, str]], enabled: bool = True):
        """state_fn() -> (state, mic_level, message); state in hidden|recording|processing|message."""
        self.root, self.state_fn, self.enabled = root, state_fn, enabled
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)
        self.levels: deque[float] = deque([0.0] * self.BARS, maxlen=self.BARS)
        self.visible = False
        self.on_state_change: Callable[[str], None] | None = None
        self._last_state = "hidden"

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.configure(bg=KEY)
        self.win.attributes("-transparentcolor", KEY)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.0)
        self.canvas = tk.Canvas(self.win, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack()
        self._size(150, 40)
        self.win.deiconify()
        self.win.update_idletasks()
        self.hwnd = int(self.win.wm_frame(), 16)
        style = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT
                              | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST)
        self.root.after(100, self._tick)

    def _size(self, w: int, h: int) -> None:
        self.w, self.h = int(w * self.scale), int(h * self.scale)
        self.canvas.configure(width=self.w, height=self.h)
        # Anti-aliased pill drawn with Pillow at 4x, then scaled down.
        s = 4
        img = Image.new("RGBA", (self.w * s, self.h * s), KEY)
        d = ImageDraw.Draw(img)
        r = self.h * s // 2
        d.rounded_rectangle([0, 0, self.w * s - 1, self.h * s - 1], radius=r, fill=BORDER)
        d.rounded_rectangle([s * 1.5, s * 1.5, self.w * s - 1 - s * 1.5, self.h * s - 1 - s * 1.5],
                            radius=r - s * 1.5, fill=BG)
        self._bg = ImageTk.PhotoImage(img.resize((self.w, self.h), Image.LANCZOS))

    def _place(self) -> None:
        left, top, right, bottom = _work_area()
        x = left + (right - left - self.w) // 2
        y = bottom - self.h - int(18 * self.scale)
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def _show(self, show: bool) -> None:
        if show == self.visible:
            return
        self.visible = show
        if show:
            self._place()
        self.win.attributes("-alpha", 0.96 if show else 0.0)

    def _draw(self, state: str, level: float, message: str) -> None:
        c, sc = self.canvas, self.scale
        c.delete("all")
        c.create_image(0, 0, image=self._bg, anchor="nw")
        cy = self.h / 2
        if state == "recording":
            pulse = 0.75 + 0.25 * math.sin(time.monotonic() * 6)
            rr = 4.5 * sc * pulse
            c.create_oval(18 * sc - rr, cy - rr, 18 * sc + rr, cy + rr, fill=RED, outline="")
            self.levels.append(level)
            bw, gap = 4 * sc, 4.5 * sc
            x0 = 34 * sc
            for i, lv in enumerate(self.levels):
                h = max(3 * sc, min(self.h - 14 * sc, (3 + lv * 26) * sc))
                x = x0 + i * (bw + gap)
                c.create_rectangle(x, cy - h / 2, x + bw, cy + h / 2, fill=FG, outline="")
        elif state == "processing":
            t = time.monotonic()
            for i in range(3):
                a = 0.5 + 0.5 * math.sin(t * 7 - i * 0.9)
                r = (2.5 + 1.8 * a) * sc
                x = self.w / 2 + (i - 1) * 16 * sc
                c.create_oval(x - r, cy - r, x + r, cy + r, fill=AMBER if a > 0.6 else FG, outline="")
        elif state == "message":
            c.create_text(self.w / 2, cy, text=message, fill=FG, font=("Segoe UI", 9))

    def _tick(self) -> None:
        try:
            state, level, message = self.state_fn()
            if state != self._last_state:
                self._last_state = state
                if state == "message":
                    width = 30 + 6.2 * len(message)
                    self._size(max(150, min(420, width)), 40)
                else:
                    self._size(150, 40)
                if self.visible:
                    self._place()
                if self.on_state_change:
                    self.on_state_change(state)
            if state == "recording" and not self.visible:
                self.levels.extend([0.0] * self.BARS)
            show = self.enabled and state != "hidden"
            self._show(show)
            if show:
                self._draw(state, level, message)
        finally:
            self.root.after(33 if self.visible else 60, self._tick)

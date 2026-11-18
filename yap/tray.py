"""System tray icon and menu."""
from __future__ import annotations

import logging
import threading
import time

import pyperclip
import pystray
from PIL import Image, ImageDraw

from . import startup
from .config import CONFIG_PATH, HISTORY_PATH, LOG_PATH

log = logging.getLogger("yap")

COLORS = {"idle": (235, 235, 240), "recording": (255, 69, 58), "processing": (255, 179, 64),
          "loading": (140, 140, 150)}


def make_icon(state: str) -> Image.Image:
    s = 256
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([8, 8, s - 8, s - 8], fill=(30, 30, 34, 255))
    c = COLORS.get(state, COLORS["idle"]) + (255,)
    # microphone: capsule, cradle, stand
    d.rounded_rectangle([98, 48, 158, 150], radius=30, fill=c)
    d.arc([72, 92, 184, 182], start=0, end=180, fill=c, width=14)
    d.line([128, 182, 128, 208], fill=c, width=14)
    d.line([96, 210, 160, 210], fill=c, width=14)
    return img


class Tray:
    def __init__(self, app):
        self.app = app
        self.state = "loading"
        self.icons = {k: make_icon(k) for k in COLORS}
        self.icon = self._new_icon()

    def _new_icon(self) -> pystray.Icon:
        app = self.app
        hk = "+".join(p.strip().capitalize() for p in app.cfg["hotkey"].split("+"))
        cfg = app.cfg
        Item, Menu = pystray.MenuItem, pystray.Menu
        menu = Menu(
            Item(lambda i: f"Yap: {app.status}", None, enabled=False),
            Item(f"Hold {hk} to dictate", None, enabled=False),
            Menu.SEPARATOR,
            Item("Copy last dictation", self._copy_last, enabled=lambda i: bool(app.last_text)),
            Item("Microphone", Menu(self._mic_items)),
            Item("AI cleanup", lambda: app.toggle("cleanup_enabled"),
                 checked=lambda i: cfg.get("cleanup_enabled", True)),
            Item("Sounds", lambda: app.toggle("sounds"), checked=lambda i: cfg.get("sounds", True)),
            Item("Show recording pill", lambda: app.toggle("show_overlay"),
                 checked=lambda i: cfg.get("show_overlay", True)),
            Item("Start with Windows", self._toggle_startup, checked=lambda i: startup.is_enabled()),
            Menu.SEPARATOR,
            Item("Edit settings", lambda: app.open_file(CONFIG_PATH)),
            Item("Reload settings", lambda: app.reload_settings()),
            Item("Open history", lambda: app.open_file(HISTORY_PATH)),
            Item("Open log", lambda: app.open_file(LOG_PATH)),
            Menu.SEPARATOR,
            Item("Restart Yap", lambda: app.restart()),
            Item("Quit Yap", lambda: app.quit()),
        )
        return pystray.Icon("Yap", self.icons[self.state], f"Yap: {app.status}"[:120], menu)

    def _mic_items(self):
        from .recorder import list_input_devices
        cfg, Item = self.app.cfg, pystray.MenuItem
        names = list_input_devices(refresh=True)
        yield Item("Windows default", lambda: self.app.set_microphone(None), radio=True,
                   checked=lambda i: not cfg.get("input_device"))
        for name in names:
            yield Item(name, (lambda n: lambda: self.app.set_microphone(n))(name), radio=True,
                       checked=(lambda n: lambda i: cfg.get("input_device") == n)(name))

    def _copy_last(self) -> None:
        if self.app.last_text:
            pyperclip.copy(self.app.last_text)

    def _toggle_startup(self) -> None:
        from .config import save_config
        want = not startup.is_enabled()
        try:
            startup.set_enabled(want)
        except OSError as exc:
            log.error("Could not change startup setting: %s", exc)
            return
        self.app.cfg["start_with_windows"] = want
        save_config(self.app.cfg)
        if want and startup.package_name():
            self.app.flash("Start Yap from the Start menu or File Explorer to make this stick", 5)

    def start(self) -> None:
        threading.Thread(target=self._keep_running, name="tray-watch", daemon=True).start()

    def _keep_running(self) -> None:
        # The tray icon is Yap's only window: if it ever fails, log why and bring it back.
        # Each attempt gets its own thread, so a failed icon's windows close with it.
        for attempt in range(1, 6):
            failed = []

            def run(icon=self.icon, attempt=attempt):
                try:
                    icon.run()
                except Exception:
                    failed.append(True)
                    log.exception("Tray icon failed (attempt %d); recreating it", attempt)

            t = threading.Thread(target=run, name="tray", daemon=True)
            t.start()
            t.join()
            if not failed:
                return  # stopped on purpose (Quit / Restart)
            time.sleep(1)
            self.icon = self._new_icon()

    def set_state(self, overlay_state: str) -> None:
        state = {"recording": "recording", "processing": "processing"}.get(overlay_state, "idle")
        if not self.app.ready and state == "idle":
            state = "loading"
        if state != self.state:
            self.state = state
            try:
                self.icon.icon = self.icons[state]
            except Exception:
                pass

    def refresh(self) -> None:
        try:
            self.icon.title = f"Yap: {self.app.status}"[:120]
            if self.app.ready and self.state == "loading":
                self.set_state("hidden")
            self.icon.update_menu()
        except Exception:
            pass

    def notify(self, text: str) -> None:
        try:
            self.icon.notify(text, "Yap")
        except Exception:
            pass

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass

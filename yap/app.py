"""Yap: hold Ctrl+Win, talk, release -> clean text is typed where your cursor is.

Threads:
  main      Tk event loop (overlay + first-run setup window)
  hook      low-level keyboard hook (pynput) -> pushes start/stop/cancel commands
  control   starts/stops the microphone
  worker    transcribe -> clean up -> paste, one dictation at a time
  loader    downloads (first run) and loads the models
  tray      system tray icon and menu (pystray)
"""
from __future__ import annotations

import ctypes
import json
import logging
import logging.handlers
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from . import __version__, assets, startup
from .config import APP_DIR, CONFIG_PATH, HISTORY_PATH, LOG_PATH, load_config

log = logging.getLogger("yap")


def _setup_logging() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.INFO)
    fh = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s: %(message)s"))
    log.addHandler(fh)
    if sys.stderr is not None:
        log.addHandler(logging.StreamHandler())


def _single_instance(wait_seconds: float = 0) -> bool:
    """True if we are the only Yap. On restart, waits for the old instance to exit."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    end = time.monotonic() + wait_seconds
    while True:
        handle = k32.CreateMutexW(None, False, "Local\\YapDictationSingleton")
        if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
            _single_instance.handle = handle
            return True
        k32.CloseHandle(ctypes.c_void_p(handle))
        if time.monotonic() > end:
            return False
        time.sleep(0.25)


def _message_box(text: str, title: str = "Yap") -> None:
    ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)


def _hotkey_label(spec: str) -> str:
    return "+".join(p.strip().capitalize() for p in spec.split("+"))


class SetupWindow:
    """Shown only while first-run downloads are in progress."""

    def __init__(self, root: tk.Tk):
        self.win = tk.Toplevel(root)
        self.win.title("Yap setup")
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", lambda: None)
        frm = ttk.Frame(self.win, padding=18)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Setting up Yap", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frm, text="Downloading the local AI models (one time only). Everything runs on this PC.",
                  wraplength=380).pack(anchor="w", pady=(4, 12))
        self.label = ttk.Label(frm, text="Starting…", width=56)
        self.label.pack(anchor="w")
        self.bar = ttk.Progressbar(frm, length=380, mode="determinate", maximum=1000)
        self.bar.pack(fill="x", pady=(6, 0))
        self.win.update_idletasks()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"+{(sw - self.win.winfo_width()) // 2}+{(sh - self.win.winfo_height()) // 3}")

    def update(self, label: str, done: int, total: int) -> None:
        if total:
            self.label.configure(text=f"{label}: {done / 1e6:,.0f} / {total / 1e6:,.0f} MB")
            self.bar.configure(value=done * 1000 / total)
        else:
            self.label.configure(text=label)

    def close(self) -> None:
        self.win.destroy()


class App:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.status = "Starting…"
        self.ready = False
        self.pending = 0
        self.last_text = ""
        self._message = ""
        self._message_until = 0.0
        self._progress: tuple[str, int, int] | None = None
        self._rec_started = 0.0
        self.ctl: queue.Queue[str] = queue.Queue()
        self.jobs: queue.Queue = queue.Queue()

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Yap")

        from .overlay import Overlay
        from .recorder import Recorder
        from .sounds import Sounds
        self.recorder = Recorder(self.cfg)
        self.sounds = Sounds(APP_DIR / "sounds", self.cfg.get("sounds", True))
        self.overlay = Overlay(self.root, self._overlay_state, self.cfg.get("show_overlay", True))
        self.transcriber = None
        self.backend = None
        self.cleaner = None
        self.hotkey = None
        self.setup_window: SetupWindow | None = None

        from .tray import Tray
        self.tray = Tray(self)
        self.overlay.on_state_change = self.tray.set_state

    # ---------- state shared with the overlay (read on the Tk thread) ----------
    def flash(self, text: str, seconds: float = 2.2) -> None:
        self._message, self._message_until = text, time.monotonic() + seconds

    def _overlay_state(self) -> tuple[str, float, str]:
        if self.recorder.recording:
            return "recording", self.recorder.level, ""
        if self.pending > 0:
            return "processing", 0.0, ""
        if time.monotonic() < self._message_until:
            return "message", 0.0, self._message
        return "hidden", 0.0, ""

    def set_status(self, text: str) -> None:
        self.status = text
        log.info("Status: %s", text)
        self.tray.refresh()

    # ---------- startup ----------
    def run(self) -> None:
        from .hotkey import HoldHotkey
        try:
            self.hotkey = HoldHotkey(self.cfg["hotkey"],
                                     lambda: self.ctl.put("start"),
                                     lambda: self.ctl.put("stop"),
                                     lambda: self.ctl.put("cancel"))
        except ValueError as exc:
            _message_box(f"Invalid hotkey in settings: {exc}\nUsing Ctrl+Win instead.")
            self.cfg["hotkey"] = "ctrl+win"
            self.hotkey = HoldHotkey("ctrl+win", lambda: self.ctl.put("start"),
                                     lambda: self.ctl.put("stop"), lambda: self.ctl.put("cancel"))
        threading.Thread(target=self._load, name="loader", daemon=True).start()
        threading.Thread(target=self._control_loop, name="control", daemon=True).start()
        threading.Thread(target=self._worker_loop, name="worker", daemon=True).start()
        threading.Thread(target=startup.apply, args=(self.cfg,), name="integration", daemon=True).start()
        self.hotkey.start()
        self.tray.start()
        self._poll_setup()
        self.root.mainloop()

    def _poll_setup(self) -> None:
        prog = self._progress
        if prog and self.setup_window is None:
            self.setup_window = SetupWindow(self.root)
        if self.setup_window is not None:
            if prog:
                self.setup_window.update(*prog)
            else:
                self.setup_window.close()
                self.setup_window = None
        self.root.after(150, self._poll_setup)

    def _on_progress(self, label: str, done: int, total: int) -> None:
        self._progress = (label, done, total)
        if total:
            self.status = f"Downloading {label} ({done * 100 // total}%)"

    def _load(self) -> None:
        try:
            from .cleanup import Cleaner, OllamaBackend
            from .transcriber import Transcriber

            self.transcriber = Transcriber(self.cfg)
            downloaded = False
            # 1) First-run downloads, with a progress window.
            first = self.transcriber.plan()[0]
            if first[0] == "cuda" and not assets.cuda_libs_available():
                downloaded = True
                self.set_status("Downloading GPU libraries…")
                try:
                    assets.ensure_cuda_libs(self._on_progress)
                except Exception as exc:  # Whisper will fall back to the CPU
                    log.warning("Could not get GPU libraries (%s); speech will run on the CPU", exc)
            if not assets.whisper_ready(first[2]):
                downloaded = True
                self.set_status("Downloading speech model…")
                assets.ensure_whisper(first[2], self._on_progress)
            builtin = self.cfg.get("cleanup_backend", "builtin") == "builtin"
            if builtin:
                flavor = assets.pick_engine_flavor(self.cfg.get("llm_engine", "auto"))
                if not assets.engine_exe(flavor).exists():
                    downloaded = True
                    self.set_status("Downloading AI engine…")
                    assets.ensure_engine(flavor, self._on_progress)
                if not assets.llm_path(self.cfg["llm_model"]).exists():
                    downloaded = True
                    self.set_status("Downloading cleanup model…")
                    assets.ensure_llm(self.cfg["llm_model"], self._on_progress)
            self._progress = None

            # 2) Load both models and keep them warm.
            self.set_status("Loading speech model…")
            self.transcriber.load()
            self.set_status("Loading cleanup model…")
            if builtin:
                from .engine import LlamaServer
                self.backend = LlamaServer(assets.engine_exe(flavor),
                                           assets.ensure_llm(self.cfg["llm_model"]),
                                           APP_DIR / "engine.log")
                self.backend.start()
            else:
                self.backend = OllamaBackend(self.cfg)
            self.cleaner = Cleaner(self.cfg, self.backend)
            # The first request can be slow (GPU shader compile); do it now, not on your first dictation.
            try:
                self.backend.chat(self.cleaner._messages("warming up the model now"), 16, 300)
            except Exception as exc:
                log.warning("Cleanup model unavailable (%s); basic cleanup will be used", exc)

            self.ready = True
            label = _hotkey_label(self.cfg["hotkey"])
            self.set_status(f"Ready ({self.transcriber.device.upper()})")
            self.flash(f"Yap is ready. Hold {label} to dictate.", 3.0)
            if downloaded:
                self.tray.notify(f"Setup finished. Hold {label}, speak, and let go.")
        except Exception as exc:
            self._progress = None
            log.exception("Startup failed")
            self.set_status("Error, see log")
            self.flash("Yap could not start. Right-click the tray icon > Open log.", 6)
            self.tray.notify(f"Yap could not start: {exc}")

    # ---------- recording ----------
    def _control_loop(self) -> None:
        while True:
            cmd = self.ctl.get()
            try:
                if cmd == "start":
                    if not self.ready:
                        self.sounds.error()
                        self.flash("Still loading, one moment…")
                        continue
                    if self.recorder.recording:
                        continue
                    try:
                        self.recorder.start()
                    except Exception as exc:
                        log.exception("Microphone error")
                        self.sounds.error()
                        self.flash(f"Microphone error: {exc}"[:80], 4)
                        continue
                    self._rec_started = time.monotonic()
                    self.sounds.start()
                elif cmd in ("stop", "cancel"):
                    if not self.recorder.recording:
                        continue
                    audio = self.recorder.stop()
                    held = time.monotonic() - self._rec_started
                    if cmd == "cancel" or held < float(self.cfg.get("min_hold_seconds", 0.35)):
                        continue
                    if self.recorder.peak < 0.003:
                        self.sounds.error()
                        self.flash("Your microphone is silent. Pick another: tray icon > Microphone", 4)
                        continue
                    self.sounds.stop()
                    self.pending += 1
                    self.jobs.put(audio)
            except Exception:
                log.exception("Control loop error")

    def _worker_loop(self) -> None:
        while True:
            audio = self.jobs.get()
            t0 = time.perf_counter()
            try:
                raw = self.transcriber.transcribe(audio)
                t1 = time.perf_counter()
                if not raw:
                    self.flash("No speech heard")
                    continue
                text, method = self.cleaner.clean(raw)
                t2 = time.perf_counter()
                if not text:
                    continue
                if self.cfg.get("append_space", True) and not text.endswith(("\n", " ")):
                    text += " "
                from .paste import paste_text
                paste_text(text, self.cfg.get("restore_clipboard", True))
                self.last_text = text.strip()
                log.info("Dictation %.1fs audio: stt %.0f ms, cleanup %.0f ms (%s)",
                         audio.size / 16000, (t1 - t0) * 1000, (t2 - t1) * 1000, method)
                if method == "basic" and self.cfg.get("cleanup_enabled", True):
                    self.flash("AI cleanup unavailable, used basic cleanup", 2.5)
                self._save_history(raw, self.last_text, method, audio.size / 16000, t2 - t0)
            except Exception:
                log.exception("Dictation failed")
                self.sounds.error()
                self.flash("Something went wrong (see log)", 3)
            finally:
                self.pending = max(0, self.pending - 1)
                self.tray.refresh()

    def _save_history(self, raw: str, text: str, method: str, seconds: float, latency: float) -> None:
        if not self.cfg.get("history_enabled", True):
            return
        try:
            with open(HISTORY_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"),
                                    "text": text, "raw": raw, "cleanup": method,
                                    "audio_seconds": round(seconds, 2),
                                    "latency_ms": round(latency * 1000)}) + "\n")
        except Exception as exc:
            log.warning("Could not write history: %s", exc)

    # ---------- tray actions ----------
    def open_file(self, path) -> None:
        if not os.path.exists(path):
            open(path, "a").close()
        subprocess.Popen(["notepad.exe", str(path)])

    def toggle(self, key: str) -> None:
        from .config import save_config
        self.cfg[key] = not self.cfg.get(key, True)
        save_config(self.cfg)
        if key == "sounds":
            self.sounds.enabled = self.cfg[key]
        if key == "show_overlay":
            self.overlay.enabled = self.cfg[key]

    def set_microphone(self, name) -> None:
        from .config import save_config
        self.cfg["input_device"] = name
        save_config(self.cfg)
        self.flash(f"Microphone: {name or 'Windows default'}", 2.5)

    def reload_settings(self) -> None:
        """Re-read settings that apply live (dictionary, cleanup on/off, extra instructions...)."""
        fresh = load_config()
        self.cfg.clear()
        self.cfg.update(fresh)
        self.sounds.enabled = self.cfg.get("sounds", True)
        self.overlay.enabled = self.cfg.get("show_overlay", True)
        self.flash("Settings reloaded. Model or hotkey changes need Restart.", 3)

    def restart(self) -> None:
        self.quit(restart=True)

    def quit(self, restart: bool = False) -> None:
        log.info("Quitting")
        try:
            if self.hotkey:
                self.hotkey.stop()
            if self.backend:
                self.backend.stop()
        finally:
            self.tray.stop()
            if restart:
                subprocess.Popen(startup.launch_command() + " --restart", close_fds=True)
            self.root.after(0, self.root.destroy)


def _log_thread_crash(args: threading.ExceptHookArgs) -> None:
    # A windowed app has no console, so without this a crashing thread dies silently.
    log.error("Thread %s crashed", getattr(args.thread, "name", "?"),
              exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


def main() -> None:
    _setup_logging()
    threading.excepthook = _log_thread_crash
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    if not _single_instance(15 if "--restart" in sys.argv else 0):
        log.info("Another Yap is already running; exiting")
        _message_box("Yap is already running. Look for its icon in the system tray.")
        return
    log.info("Yap %s starting (models: %s, settings: %s)", __version__, assets.ASSET_DIR, CONFIG_PATH)
    try:
        App().run()
    except Exception as exc:
        log.exception("Fatal error")
        # Release the single-instance lock first so Yap can be relaunched while this box is open.
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(getattr(_single_instance, "handle", None)))
        _message_box(f"Yap crashed: {exc}\n\nLog: {LOG_PATH}")
    os._exit(0)  # don't wait on daemon threads / audio streams

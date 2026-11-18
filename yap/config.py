"""Settings, stored as JSON in %USERPROFILE%\\.yap\\config.json."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("yap")

# Your own data (settings, history, logs) lives in %USERPROFILE%\.yap, like Ollama's .ollama.
# (Not AppData: packaged apps get AppData silently redirected.) Models and the engine live
# next to the app instead (see assets.py).
APP_DIR = Path(os.environ.get("YAP_HOME") or (Path.home() / ".yap"))


def _adopt_old_folder() -> None:
    """Yap used to be called Murmur: carry its settings and history over."""
    old = Path.home() / ".murmur"
    if "YAP_HOME" not in os.environ and not APP_DIR.exists() and (old / "config.json").exists():
        try:
            old.rename(APP_DIR)
        except OSError:
            pass


_adopt_old_folder()
CONFIG_PATH = APP_DIR / "config.json"
HISTORY_PATH = APP_DIR / "history.jsonl"
LOG_PATH = APP_DIR / "yap.log"

DEFAULTS: dict = {
    # Hold these keys to record; release to transcribe + paste.
    # Tokens: ctrl, win, alt, shift, space, f1..f24, caps_lock, scroll_lock,
    # pause, insert, menu, or a single letter/digit. Joined with "+".
    "hotkey": "ctrl+win",
    # Presses shorter than this are treated as accidental and ignored.
    "min_hold_seconds": 0.35,

    # --- Speech to text (faster-whisper) ---
    # GPU model. Options: tiny.en, base.en, small.en, medium.en, large-v3-turbo, large-v3, distil-large-v3
    "whisper_model": "large-v3-turbo",
    # Model to use if no working NVIDIA GPU is found (smaller = faster on CPU).
    "whisper_model_cpu": "small.en",
    "whisper_device": "auto",          # auto | cuda | cpu
    # GPU precision: int8_float16 (8-bit weights, about half the VRAM) or float16
    "whisper_compute_type": "int8_float16",
    "language": "en",                  # or null to auto-detect
    "beam_size": 5,
    "input_device": None,              # null = Windows default microphone, or a device name/index

    # --- AI cleanup ---
    "cleanup_enabled": True,
    "cleanup_backend": "builtin",      # builtin (bundled llama.cpp, no other software) | ollama
    # builtin model: qwen3.5-2b (default, best balance), qwen3.5-0.8b (lightest), qwen3.5-4b, or a path to any .gguf
    "llm_model": "qwen3.5-2b",
    "llm_engine": "auto",              # auto | vulkan (any GPU) | cuda (NVIDIA) | cpu
    "cleanup_timeout_seconds": 20,
    # Only allow the cleanup model to use words you actually said (it can delete, punctuate and
    # format, but never add an answer). Strongly recommended, especially for small models.
    "cleanup_word_lock": True,
    # Extra instructions appended to the cleanup prompt, e.g. "Use British spelling."
    "cleanup_extra_instructions": "",
    # Only used when cleanup_backend is "ollama":
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "qwen3.5:4b",
    "ollama_keep_alive": "60m",

    # Words/names Whisper should spell your way (also given to the cleanup model).
    "dictionary": ["Ollama", "Whisper"],

    # --- Output ---
    "append_space": True,              # add a trailing space so back-to-back dictations flow
    "restore_clipboard": True,         # put your previous clipboard text back after pasting
    "sounds": True,
    "show_overlay": True,
    "history_enabled": True,           # keep a local log of dictations (history.jsonl)

    # --- Windows integration (applied every time Yap starts) ---
    "start_with_windows": None,        # true / false; null = leave as is (tray menu toggles it)
    "start_menu_shortcut": True,       # keep a "Yap" shortcut in the Start menu
}


def load_config() -> dict:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                cfg.update(user)
        except Exception as exc:  # keep running on a broken file, but say so
            log.error("Could not read %s (%s); using defaults", CONFIG_PATH, exc)
            return cfg
    # Write back so newly added settings show up in the file.
    save_config(cfg)
    return cfg


def save_config(cfg: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)

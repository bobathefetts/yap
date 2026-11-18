"""Microphone capture at 16 kHz mono (what Whisper expects)."""
from __future__ import annotations

import logging
import threading

import numpy as np
import sounddevice as sd

log = logging.getLogger("yap")

TARGET_RATE = 16000


def _mme_index() -> int | None:
    for i, api in enumerate(sd.query_hostapis()):
        if api["name"] == "MME":
            return i
    return None


def list_input_devices(refresh: bool = False) -> list[str]:
    """Microphone names as Windows shows them (one entry per device)."""
    if refresh:
        try:  # pick up mics plugged in since launch
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
    mme = _mme_index()
    names = []
    for d in sd.query_devices():
        if d["max_input_channels"] > 0 and d["hostapi"] == mme and "Sound Mapper" not in d["name"]:
            if d["name"] not in names:
                names.append(d["name"])
    return names


def resolve_device(setting):
    """Turn the input_device setting (None, index, or name) into a sounddevice device index."""
    if setting in (None, "", "default"):
        return None
    if isinstance(setting, int):
        return setting
    mme = _mme_index()
    devices = list(enumerate(sd.query_devices()))
    for exact in (True, False):
        for i, d in devices:
            if d["max_input_channels"] > 0 and d["hostapi"] == mme:
                if (d["name"] == setting) if exact else (setting.lower() in d["name"].lower()):
                    return i
    log.warning("Microphone %r not found; using the Windows default", setting)
    return None


class Recorder:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.device = None
        self.peak = 0.0
        self.level = 0.0  # 0..1, read by the overlay meter
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._rate = TARGET_RATE
        self._lock = threading.Lock()

    def _callback(self, indata, frames, time_info, status) -> None:
        mono = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
        self._chunks.append(mono)
        rms = float(np.sqrt(np.mean(mono * mono))) if mono.size else 0.0
        self.level = min(1.0, rms * 12)

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                return
            self._chunks = []
            self.level = 0.0
            self.device = resolve_device(self.cfg.get("input_device"))
            try:
                self._rate = TARGET_RATE
                self._stream = sd.InputStream(samplerate=TARGET_RATE, channels=1, dtype="float32",
                                              device=self.device, blocksize=0, callback=self._callback)
            except Exception as exc:
                # Some devices refuse 16 kHz; record at their native rate and resample later.
                info = sd.query_devices(self.device, "input")
                self._rate = int(info["default_samplerate"])
                log.info("16 kHz not supported (%s); recording at %d Hz", exc, self._rate)
                self._stream = sd.InputStream(samplerate=self._rate, channels=1, dtype="float32",
                                              device=self.device, blocksize=0, callback=self._callback)
            self._stream.start()

    def stop(self) -> np.ndarray:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:
                log.warning("Error closing mic stream: %s", exc)
        self.level = 0.0
        if not self._chunks:
            self.peak = 0.0
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(self._chunks).astype(np.float32)
        self._chunks = []
        self.peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if self._rate != TARGET_RATE and audio.size:
            n = int(audio.size * TARGET_RATE / self._rate)
            audio = np.interp(np.linspace(0, audio.size - 1, n), np.arange(audio.size), audio).astype(np.float32)
        return audio

    @property
    def recording(self) -> bool:
        return self._stream is not None

"""Short start/stop chimes, generated once and played asynchronously."""
from __future__ import annotations

import io
import logging
import wave
import winsound
from pathlib import Path

import numpy as np

log = logging.getLogger("yap")


def _tone(path: Path, freqs: list[float], dur: float = 0.07, volume: float = 0.18) -> None:
    rate = 44100
    parts = []
    for f in freqs:
        t = np.arange(int(rate * dur)) / rate
        env = np.minimum(1, t / 0.008) * np.exp(-t * 28)
        parts.append(np.sin(2 * np.pi * f * t) * env)
    audio = (np.concatenate(parts) * volume * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(audio.tobytes())
    path.write_bytes(buf.getvalue())


class Sounds:
    def __init__(self, folder: Path, enabled: bool = True):
        self.enabled = enabled
        folder.mkdir(parents=True, exist_ok=True)
        self.start_wav = folder / "start.wav"
        self.stop_wav = folder / "stop.wav"
        self.error_wav = folder / "error.wav"
        try:
            if not self.start_wav.exists():
                _tone(self.start_wav, [660, 990])
            if not self.stop_wav.exists():
                _tone(self.stop_wav, [990, 660])
            if not self.error_wav.exists():
                _tone(self.error_wav, [300, 220], dur=0.1)
        except Exception as exc:
            log.warning("Could not create sounds: %s", exc)
            self.enabled = False

    def _play(self, path: Path) -> None:
        if self.enabled and path.exists():
            try:
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            except Exception:
                pass

    def start(self) -> None:
        self._play(self.start_wav)

    def stop(self) -> None:
        self._play(self.stop_wav)

    def error(self) -> None:
        self._play(self.error_wav)

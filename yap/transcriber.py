"""Local speech-to-text with faster-whisper (GPU if available, CPU otherwise)."""
from __future__ import annotations

import logging
import os
import sys
import time

import numpy as np

from .assets import CUDA_DIR, _pip_cublas_dir, ensure_whisper, has_nvidia_gpu

log = logging.getLogger("yap")

SAMPLE_RATE = 16000

# Things Whisper likes to "hear" in silence or noise.
_HALLUCINATIONS = {
    "thank you", "thank you.", "thanks for watching!", "thanks for watching.",
    "thank you for watching.", "you", "bye.", "bye", ".", "okay.", "so",
    "subtitles by the amara.org community",
}


_dll_dirs_added: set[str] = set()


def add_cuda_dll_dirs() -> None:
    """Make NVIDIA cuBLAS findable: Yap's own copy (the cuda folder next to the app, downloaded
    on first launch if missing) or a pip install (running from source). Safe to call repeatedly."""
    if sys.platform != "win32":
        return
    dirs = [CUDA_DIR]
    pip_dir = _pip_cublas_dir()
    if pip_dir:
        dirs.append(pip_dir)
    for d in dirs:
        key = str(d).lower()
        if key in _dll_dirs_added or not d.is_dir():
            continue
        _dll_dirs_added.add(key)
        try:
            os.add_dll_directory(str(d))
        except OSError:
            pass
        os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


add_cuda_dll_dirs()

from faster_whisper import WhisperModel  # noqa: E402  (must come after the DLL setup)


class Transcriber:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.model: WhisperModel | None = None
        self.device = "?"
        self.model_name = "?"

    def plan(self) -> list[tuple[str, str, str]]:
        """(device, compute_type, model_name) attempts, best first."""
        want = (self.cfg.get("whisper_device") or "auto").lower()
        attempts = []
        if want == "cuda" or (want == "auto" and has_nvidia_gpu()):
            attempts.append(("cuda", self.cfg.get("whisper_compute_type") or "int8_float16",
                             self.cfg["whisper_model"]))
        if want in ("auto", "cpu"):
            attempts.append(("cpu", "int8", self.cfg.get("whisper_model_cpu") or self.cfg["whisper_model"]))
        return attempts

    def load(self, progress=None) -> None:
        last_err: Exception | None = None
        for device, compute, name in self.plan():
            try:
                t0 = time.perf_counter()
                path = ensure_whisper(name, progress) if progress else ensure_whisper(name)
                if device == "cuda":
                    add_cuda_dll_dirs()  # cuBLAS may have been downloaded after startup
                log.info("Loading Whisper %s on %s (%s)...", name, device, compute)
                model = WhisperModel(str(path), device=device, compute_type=compute,
                                     cpu_threads=min(8, os.cpu_count() or 4))
                self._warmup(model)
                self.model, self.device, self.model_name = model, device, name
                log.info("Whisper ready on %s in %.1fs", device, time.perf_counter() - t0)
                return
            except Exception as exc:
                last_err = exc
                log.warning("Whisper on %s failed: %s", device, exc)
        raise RuntimeError(f"Could not load Whisper: {last_err}")

    @staticmethod
    def _warmup(model: WhisperModel) -> None:
        # Runs the encoder/decoder once so GPU problems show up now, not on first use.
        noise = (np.random.default_rng(0).standard_normal(SAMPLE_RATE) * 0.01).astype(np.float32)
        segments, _ = model.transcribe(noise, beam_size=1, vad_filter=False, language="en")
        for _ in segments:
            pass
        # Also load the voice-activity model now.
        segments, _ = model.transcribe(noise, beam_size=1, vad_filter=True, language="en")
        for _ in segments:
            pass

    def transcribe(self, audio: np.ndarray) -> str:
        if self.model is None:
            raise RuntimeError("Whisper model not loaded yet")
        if audio.size == 0 or float(np.max(np.abs(audio))) < 0.004:
            return ""  # effectively silence
        dictionary = [w for w in self.cfg.get("dictionary") or [] if w]
        segments, _info = self.model.transcribe(
            audio,
            language=self.cfg.get("language") or None,
            beam_size=int(self.cfg.get("beam_size") or 5) if self.device == "cuda" else 1,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 300},
            condition_on_previous_text=False,
            hotwords=", ".join(dictionary) if dictionary else None,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        if text.lower() in _HALLUCINATIONS and audio.size < SAMPLE_RATE * 3:
            log.info("Dropping likely hallucination: %r", text)
            return ""
        return text

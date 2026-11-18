"""Compare Whisper settings: VRAM, speed and transcript, on the sample WAVs.

Usage: python tests/benchmark_whisper.py [model] (default large-v3-turbo)
"""
import gc
import os
import subprocess
import sys
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from yap.transcriber import Transcriber  # noqa: E402  (sets up the CUDA DLLs)
from yap.config import DEFAULTS  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def vram() -> int:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout
    return int(out.strip().splitlines()[0])


def load_wav(name: str) -> np.ndarray:
    with wave.open(os.path.join(HERE, name)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else "large-v3-turbo"
    clips = {n: load_wav(n) for n in ("sample_messy.wav", "sample_long.wav")}
    for compute, beam in (("float16", 5), ("int8_float16", 5), ("int8_float16", 1)):
        gc.collect()
        time.sleep(1.5)
        base = vram()
        cfg = dict(DEFAULTS, whisper_model=model, whisper_device="cuda", whisper_compute_type=compute, beam_size=beam)
        t = Transcriber(cfg)
        t.load()
        time.sleep(0.5)
        print(f"\n=== {model} {compute} beam={beam}: VRAM +{vram() - base} MB ===", flush=True)
        for name, audio in clips.items():
            t.transcribe(audio)  # warm
            runs = []
            for _ in range(3):
                t0 = time.perf_counter()
                text = t.transcribe(audio)
                runs.append((time.perf_counter() - t0) * 1000)
            print(f"  {name} ({audio.size / 16000:.1f}s): {min(runs):.0f} ms\n    {text}", flush=True)
        del t
        gc.collect()


if __name__ == "__main__":
    main()

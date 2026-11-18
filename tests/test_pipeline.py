"""End-to-end check without the keyboard: mic opens, WAV -> Whisper -> cleanup.
Usage: python tests/test_pipeline.py [file.wav]"""
import logging
import os
import sys
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
from yap import assets  # noqa: E402
from yap.cleanup import Cleaner  # noqa: E402
from yap.config import DEFAULTS  # noqa: E402
from yap.engine import LlamaServer  # noqa: E402
from yap.recorder import Recorder  # noqa: E402
from yap.transcriber import Transcriber  # noqa: E402

cfg = dict(DEFAULTS)
wav = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "sample_messy.wav")

rec = Recorder(cfg)
rec.start(); time.sleep(1.0); audio = rec.stop()
print(f"MIC: captured {audio.size / 16000:.2f}s, peak {float(np.max(np.abs(audio))) if audio.size else 0:.3f}")

last = {}
def prog(label, done, total):
    pct = done * 100 // total if total else 0
    if pct >= last.get(label, -25) + 25:
        last[label] = pct; print(f"  {label}: {pct}%", flush=True)

t = Transcriber(cfg); t.load(prog)
srv = LlamaServer(assets.ensure_engine("vulkan"), assets.ensure_llm(cfg["llm_model"]), assets.DATA_DIR / "engine-test.log")
srv.start()
c = Cleaner(cfg, srv)
c.clean("warm up the prompt cache please okay")

with wave.open(wav) as w:
    audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
t0 = time.perf_counter(); raw = t.transcribe(audio); t1 = time.perf_counter()
text, how = c.clean(raw); t2 = time.perf_counter()
print(f"\nWHISPER ({t.device}, {(t1 - t0) * 1000:.0f} ms): {raw}")
print(f"CLEANED ({how}, {(t2 - t1) * 1000:.0f} ms): {text}")
print(f"TOTAL after release: {(t2 - t0) * 1000:.0f} ms for {audio.size / 16000:.1f}s of speech")
srv.stop()

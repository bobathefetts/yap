"""Runs llama.cpp's llama-server as a hidden child process that dies with Yap."""
from __future__ import annotations

import ctypes
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger("yap")

CREATE_NO_WINDOW = 0x08000000


class _JobKillOnClose:
    """Windows Job Object: when Yap exits (even if it crashes) the engine is killed too."""

    def __init__(self) -> None:
        self.handle = None
        try:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                            ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class EXTENDED(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC),
                            ("IoInfo", IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k32.CreateJobObjectW.restype = wintypes.HANDLE
            job = k32.CreateJobObjectW(None, None)
            info = EXTENDED()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
            if job and ok:
                self.handle = job
                self._k32 = k32
        except Exception as exc:
            log.warning("Job object unavailable: %s", exc)

    def add(self, proc: subprocess.Popen) -> None:
        if self.handle:
            self._k32.AssignProcessToJobObject(wintypes.HANDLE(self.handle),
                                               wintypes.HANDLE(int(proc._handle)))


# llama-server.exe needs Microsoft's C++ runtime, which a fresh Windows install may lack.
# Yap.exe ships these DLLs, so hand the engine a folder with just these three.
_CPP_RUNTIME = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")


def _cpp_runtime_dir(dest: Path) -> Path | None:
    sources = [Path(p) for p in (getattr(sys, "_MEIPASS", None), sys.base_prefix,
                                 os.path.dirname(sys.executable)) if p]
    try:
        dest.mkdir(parents=True, exist_ok=True)
        for name in _CPP_RUNTIME:
            if (dest / name).exists():
                continue
            src = next((d / name for d in sources if (d / name).exists()), None)
            if src:
                shutil.copy2(src, dest / name)
    except OSError as exc:
        log.warning("Could not prepare the C++ runtime for the engine: %s", exc)
    return dest if any((dest / n).exists() for n in _CPP_RUNTIME) else None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LlamaServer:
    def __init__(self, exe: Path, model: Path, log_path: Path, ctx: int = 4096):
        self.exe, self.model, self.log_path, self.ctx = exe, model, log_path, ctx
        self.port = 0
        self.proc: subprocess.Popen | None = None
        self._job = _JobKillOnClose()
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 180) -> None:
        with self._lock:
            if self.proc and self.proc.poll() is None:
                return
            self.port = _free_port()
            args = [str(self.exe), "-m", str(self.model),
                    "--host", "127.0.0.1", "--port", str(self.port),
                    "-ngl", "999",          # put every layer on the GPU (ignored on CPU builds)
                    "-c", str(self.ctx), "-np", "1",
                    "--reasoning", "off",     # no "thinking" output: we want speed
                    "--load-mode", "none",    # no memory-mapping: weights live in VRAM, not also in RAM
                    "--no-webui"]
            log.info("Starting engine: %s", " ".join(args))
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            logf = open(self.log_path, "w", encoding="utf-8", errors="replace")
            # Ignore LLAMA_ARG_* variables other AI tools may have set system-wide; they would
            # silently change our settings (context size, GPU layers, ...).
            env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("LLAMA_ARG_", "LLAMA_CACHE"))}
            runtime = _cpp_runtime_dir(self.exe.parent.parent / "runtime")
            if runtime:
                # Only used if Windows doesn't already have these DLLs (System32 is searched first).
                env["PATH"] = str(runtime) + os.pathsep + env.get("PATH", "")
            self.proc = subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, cwd=str(self.exe.parent),
                                         env=env, creationflags=CREATE_NO_WINDOW)
            self._job.add(self.proc)
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"Engine exited with code {self.proc.returncode}; see {self.log_path}")
            try:
                with urllib.request.urlopen(self.url + "/health", timeout=2) as r:
                    if r.status == 200:
                        log.info("Engine ready on port %d in %.1fs", self.port, time.monotonic() - t0)
                        return
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                pass
            time.sleep(0.25)
        raise TimeoutError("Engine did not become ready in time")

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def chat(self, messages: list[dict], max_tokens: int, timeout: float, grammar: str | None = None) -> str:
        if not self.alive():
            log.warning("Engine not running; restarting")
            self.start()
        body = {
            "messages": messages,
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": max_tokens,
            "cache_prompt": True,  # reuse the processed system prompt between dictations
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if grammar:
            body["grammar"] = grammar
        req = urllib.request.Request(self.url + "/v1/chat/completions",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        return data["choices"][0]["message"].get("content") or ""

    def stop(self) -> None:
        with self._lock:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            self.proc = None

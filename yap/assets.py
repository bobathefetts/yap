"""Locates (and if missing, downloads) what Yap runs: the llama.cpp engine, the models
and NVIDIA cuBLAS.

They live INSIDE the app folder, next to Yap.exe (or in the project folder when running
from source), so the folder is self-contained: copy or zip it and it runs anywhere, offline.
Personal things (settings, history, logs) stay per-user in %USERPROFILE%\\.yap instead,
so sharing the app folder never shares them.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

log = logging.getLogger("yap")

from .config import APP_DIR


def _app_root() -> Path:
    if getattr(sys, "frozen", False):  # Yap.exe: its own folder
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent  # source checkout: the project folder


DATA_DIR = APP_DIR  # per-user: settings, history, logs
ASSET_DIR = Path(os.environ.get("YAP_ASSETS") or _app_root())  # shipped with the app
ENGINE_DIR = ASSET_DIR / "engine"
MODELS_DIR = ASSET_DIR / "models"
WHISPER_DIR = MODELS_DIR / "whisper"
DOWNLOADS_DIR = ASSET_DIR / "downloads"

# Pinned llama.cpp release (https://github.com/ggml-org/llama.cpp/releases).
LLAMA_BUILD = "b12421"
_GH = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_BUILD}"
ENGINE_FLAVORS: dict[str, list[str]] = {
    # flavor -> zip files to download and extract into the same folder
    "vulkan": [f"{_GH}/llama-{LLAMA_BUILD}-bin-win-vulkan-x64.zip"],
    "cuda": [f"{_GH}/llama-{LLAMA_BUILD}-bin-win-cuda-13.5-x64.zip",
             f"{_GH}/cudart-llama-bin-win-cuda-13.5-x64.zip"],
    "cpu": [f"{_GH}/llama-{LLAMA_BUILD}-bin-win-cpu-x64.zip"],
}

# Cleanup models people can pick in settings ("llm_model"). Small, instruction-following, Apache-2.0.
LLM_MODELS: dict[str, dict] = {
    "qwen3.5-4b": {"repo": "unsloth/Qwen3.5-4B-GGUF", "file": "Qwen3.5-4B-Q4_K_M.gguf"},
    "qwen3.5-2b": {"repo": "unsloth/Qwen3.5-2B-GGUF", "file": "Qwen3.5-2B-Q4_K_M.gguf"},
    "qwen3.5-0.8b": {"repo": "unsloth/Qwen3.5-0.8B-GGUF", "file": "Qwen3.5-0.8B-Q4_K_M.gguf"},
}

Progress = Callable[[str, int, int], None]  # (label, bytes_done, bytes_total)


def _noop(label: str, done: int, total: int) -> None:
    pass


def download(url: str, dest: Path, label: str, progress: Progress = _noop) -> Path:
    """Stream url to dest (via a .part file so a broken download is never mistaken for a good one)."""
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    log.info("Downloading %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": "Yap-dictation"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done, last = 0, 0.0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            now = time.monotonic()
            if now - last > 0.2:
                progress(label, done, total)
                last = now
        progress(label, done, total or done)
    if total and part.stat().st_size != total:
        part.unlink(missing_ok=True)
        raise IOError(f"Incomplete download of {url}")
    part.replace(dest)
    return dest


def has_nvidia_gpu() -> bool:
    try:
        out = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.returncode == 0 and "GPU" in out.stdout
    except Exception:
        return False


def pick_engine_flavor(setting: str) -> str:
    setting = (setting or "auto").lower()
    if setting in ENGINE_FLAVORS:
        return setting
    # Vulkan runs on NVIDIA, AMD and Intel GPUs and is a 40 MB download.
    return "vulkan"


def engine_exe(flavor: str) -> Path:
    return ENGINE_DIR / f"{LLAMA_BUILD}-{flavor}" / "llama-server.exe"


def ensure_engine(flavor: str, progress: Progress = _noop) -> Path:
    exe = engine_exe(flavor)
    if exe.exists():
        return exe
    target = exe.parent
    tmp = target.with_name(target.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for url in ENGINE_FLAVORS[flavor]:
        zpath = DOWNLOADS_DIR / url.rsplit("/", 1)[1]
        download(url, zpath, f"AI engine ({flavor})", progress)
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(tmp)
    # Some release zips nest everything in a folder; flatten so llama-server.exe is at the top.
    found = next(tmp.rglob("llama-server.exe"), None)
    if found is None:
        raise FileNotFoundError("llama-server.exe not found in the downloaded engine")
    if found.parent != tmp:
        for item in found.parent.iterdir():
            shutil.move(str(item), tmp / item.name)
    shutil.rmtree(target, ignore_errors=True)
    tmp.rename(target)
    shutil.rmtree(DOWNLOADS_DIR, ignore_errors=True)
    return exe


# GPU Whisper only needs NVIDIA's cuBLAS (tested: cuDNN is never loaded). The packaged app
# downloads NVIDIA's official wheel from PyPI once and keeps just these two DLLs.
CUDA_DIR = ASSET_DIR / "cuda"
CUBLAS_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll")
CUBLAS_WHEEL = {
    "url": "https://files.pythonhosted.org/packages/20/e2/fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/"
           "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl",
    "sha256": "623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661",
}


def _pip_cublas_dir() -> Path | None:
    """cuBLAS from a pip install (running from source), if present."""
    import site
    import sys
    roots = [Path(sys.prefix) / "Lib" / "site-packages"]
    try:
        roots += [Path(p) for p in site.getsitepackages()]
    except Exception:  # not available in the packaged app
        pass
    for r in roots:
        d = r / "nvidia" / "cublas" / "bin"
        if (d / CUBLAS_DLLS[0]).exists():
            return d
    return None


def cuda_libs_available() -> bool:
    return all((CUDA_DIR / n).exists() for n in CUBLAS_DLLS) or _pip_cublas_dir() is not None


def ensure_cuda_libs(progress: Progress = _noop) -> Path:
    if all((CUDA_DIR / n).exists() for n in CUBLAS_DLLS):
        return CUDA_DIR
    import hashlib
    wheel = DOWNLOADS_DIR / CUBLAS_WHEEL["url"].rsplit("/", 1)[1]
    download(CUBLAS_WHEEL["url"], wheel, "GPU libraries (NVIDIA cuBLAS)", progress)
    h = hashlib.sha256()
    with open(wheel, "rb") as f:
        while chunk := f.read(1 << 22):
            h.update(chunk)
    if h.hexdigest() != CUBLAS_WHEEL["sha256"]:
        wheel.unlink(missing_ok=True)
        raise IOError("GPU library download is corrupted (checksum mismatch); it will be retried next start")
    CUDA_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel) as zf:
        for name in zf.namelist():
            if name.rsplit("/", 1)[-1] in CUBLAS_DLLS:
                part = CUDA_DIR / (name.rsplit("/", 1)[-1] + ".part")
                with zf.open(name) as src, open(part, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 22)
                part.replace(CUDA_DIR / name.rsplit("/", 1)[-1])
    wheel.unlink(missing_ok=True)
    return CUDA_DIR


_WHISPER_REPOS = {
    "tiny.en": "Systran/faster-whisper-tiny.en", "base.en": "Systran/faster-whisper-base.en",
    "small.en": "Systran/faster-whisper-small.en", "medium.en": "Systran/faster-whisper-medium.en",
    "small": "Systran/faster-whisper-small", "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
}
_WHISPER_FILES = ("config.json", "preprocessor_config.json", "model.bin", "tokenizer.json")


def whisper_dir(name: str) -> Path:
    repo = _WHISPER_REPOS.get(name, name)
    return WHISPER_DIR / repo.replace("/", "--")


def whisper_ready(name: str) -> bool:
    return (whisper_dir(name) / ".complete").exists()


def ensure_whisper(name: str, progress: Progress = _noop) -> Path:
    """Download a faster-whisper model (by short name or 'org/repo') into our models folder."""
    target = whisper_dir(name)
    if whisper_ready(name):
        return target
    repo = _WHISPER_REPOS.get(name, name)
    req = urllib.request.Request(f"https://huggingface.co/api/models/{repo}",
                                 headers={"User-Agent": "Yap-dictation"})
    import json
    with urllib.request.urlopen(req, timeout=30) as r:
        files = [s["rfilename"] for s in json.loads(r.read()).get("siblings", [])]
    wanted = [f for f in files if f in _WHISPER_FILES or f.startswith("vocabulary.")]
    if "model.bin" not in wanted:
        raise FileNotFoundError(f"{repo} does not look like a faster-whisper model")
    for f in wanted:
        download(f"https://huggingface.co/{repo}/resolve/main/{f}", target / f,
                 f"Speech model ({name})", progress)
    (target / ".complete").touch()
    return target


def llm_path(key: str) -> Path:
    if key not in LLM_MODELS:
        return Path(key)
    spec = LLM_MODELS[key]
    return MODELS_DIR / "llm" / spec["file"]


def ensure_llm(key: str, progress: Progress = _noop) -> Path:
    if key not in LLM_MODELS:
        # Allow a direct path to any .gguf file.
        p = Path(key)
        if p.suffix.lower() == ".gguf" and p.exists():
            return p
        raise ValueError(f"Unknown llm_model {key!r}. Options: {', '.join(LLM_MODELS)} or a path to a .gguf")
    spec = LLM_MODELS[key]
    url = f"https://huggingface.co/{spec['repo']}/resolve/main/{spec['file']}"
    return download(url, llm_path(key), f"Cleanup model ({key})", progress)

"""Make the files to send to other people, in release\\:

  Yap-Setup.exe          small installer (app + AI engine); Yap downloads the models
                         that PC needs on first launch.
  Yap-Setup-Offline.zip  the same installer plus every model and GPU library (about 4 GB);
                         installs with no internet.

Needs dist\\Yap (build.bat) and Inno Setup 6 (winget install JRSoftware.InnoSetup)."""
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "dist" / "Yap"
OUT = ROOT / "release"
SETUP = OUT / "Yap-Setup.exe"
OFFLINE = OUT / "Yap-Setup-Offline.zip"
TOP = "Yap Setup"  # folder inside the offline zip
BUNDLED = ("models", "cuda")  # what the offline zip adds next to the installer
STORE = {".gguf", ".bin", ".exe", ".zip"}  # already compressed, or doesn't shrink


def find_iscc() -> Path | None:
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            for sub in ("Programs/Inno Setup 6", "Inno Setup 6"):
                exe = Path(base) / sub / "ISCC.exe"
                if exe.exists():
                    return exe
    return None


def version() -> str:
    sys.path.insert(0, str(ROOT))
    from yap import __version__
    return __version__


def build_setup(iscc: Path) -> None:
    t0 = time.time()
    subprocess.run([str(iscc), "/Q", f"/DAppVersion={version()}", str(ROOT / "installer" / "yap.iss")], check=True)
    print(f"Created {SETUP} ({SETUP.stat().st_size / 1e6:.0f} MB) in {time.time() - t0:.0f}s")


def build_offline_zip() -> None:
    t0 = time.time()
    entries = [(SETUP, f"{TOP}/{SETUP.name}")]
    readme = ROOT / "assets" / "READ ME.txt"
    if readme.exists():
        entries.append((readme, f"{TOP}/READ ME.txt"))
    for folder in BUNDLED:
        for f in sorted((ROOT / folder).rglob("*")):
            if f.is_file() and not f.name.endswith(".part"):
                entries.append((f, f"{TOP}/{f.relative_to(ROOT).as_posix()}"))
    tmp = OFFLINE.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", allowZip64=True) as zf:
        for src, name in entries:
            method = zipfile.ZIP_STORED if src.suffix.lower() in STORE else zipfile.ZIP_DEFLATED
            zf.write(src, name, compress_type=method)
    tmp.replace(OFFLINE)
    print(f"Created {OFFLINE} ({OFFLINE.stat().st_size / 1e9:.2f} GB) in {time.time() - t0:.0f}s")


def main() -> int:
    if not (APP / "Yap.exe").exists():
        print("Build first: build.bat")
        return 1
    iscc = find_iscc()
    if not iscc:
        print("Inno Setup 6 is needed to make the installer:  winget install JRSoftware.InnoSetup")
        return 1
    missing = [f for f in BUNDLED if not (ROOT / f).is_dir()]
    OUT.mkdir(exist_ok=True)
    build_setup(iscc)
    if missing:
        print(f"Skipping the offline zip: {', '.join(missing)} not found in {ROOT}.")
    else:
        build_offline_zip()
    return 0


if __name__ == "__main__":
    sys.exit(main())

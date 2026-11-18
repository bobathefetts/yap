"""After PyInstaller: put the models, engine and GPU libraries inside dist\\Yap so the
folder is self-contained. Uses hard links (instant, no extra disk space) and falls back to
copying when that isn't possible (e.g. another drive)."""
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "dist" / "Yap"
FOLDERS = ("models", "engine", "cuda")


def link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def main() -> int:
    if not (APP / "Yap.exe").exists():
        print("dist\\Yap\\Yap.exe not found: build first.")
        return 1
    missing = [f for f in FOLDERS if not (ROOT / f).is_dir()]
    if missing:
        print(f"Warning: {', '.join(missing)} not found in {ROOT}. Yap will download them on first launch.")
    total = 0
    for folder in FOLDERS:
        src_root = ROOT / folder
        if not src_root.is_dir():
            continue
        for src in src_root.rglob("*"):
            if src.is_file() and not src.name.endswith(".part"):
                link_or_copy(src, APP / src.relative_to(ROOT))
                total += src.stat().st_size
    app_size = sum(f.stat().st_size for f in APP.rglob("*") if f.is_file())
    print(f"Added {total / 1e9:.2f} GB of models/engine/GPU libraries. dist\\Yap is now {app_size / 1e9:.2f} GB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

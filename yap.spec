# PyInstaller recipe for Yap.exe  (build with build.bat)
#
# Produces dist\Yap\ : Yap.exe plus its libraries (~270 MB). build.bat then runs
# tools\assemble.py, which adds the models, the llama.cpp engine and NVIDIA cuBLAS.
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

datas = collect_data_files("faster_whisper")          # Silero VAD model (assets/*.onnx)
binaries = collect_dynamic_libs("ctranslate2")        # ctranslate2.dll, libiomp5md.dll, cudnn64_9.dll

a = Analysis(
    ["yap.pyw"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "pystray._win32",
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        "pynput._util.win32",
    ],
    excludes=[
        "nvidia",            # cuBLAS is downloaded on first launch instead (550 MB)
        "torch", "tensorflow", "matplotlib", "pandas", "scipy", "IPython", "notebook",
        "PyInstaller",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Yap",
    icon="assets/yap.ico",
    console=False,           # tray app: no console window
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Yap")

@echo off
setlocal
rem Builds dist\Yap: Yap.exe plus models, engine and GPU libraries (self-contained).
rem Run setup.bat once first. Close Yap before building (Windows locks a running exe).
set "PY=%USERPROFILE%\.yap\venv\Scripts\python.exe"
if not exist "%PY%" (echo Run setup.bat first. & exit /b 1)
cd /d "%~dp0"
tasklist /FI "IMAGENAME eq Yap.exe" | find /I "Yap.exe" >nul && (echo Yap is running. Quit it from the tray icon first. & exit /b 1)
uv pip install --python "%USERPROFILE%\.yap\venv" pyinstaller >nul || exit /b 1
"%PY%" -c "import sys; sys.path.insert(0, '.'); from yap.tray import make_icon; import os; os.makedirs('assets', exist_ok=True); make_icon('idle').save('assets/yap.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])" || exit /b 1
"%PY%" -m PyInstaller yap.spec --noconfirm --clean || exit /b 1
"%PY%" tools\assemble.py || exit /b 1
echo.
echo Built: %~dp0dist\Yap\Yap.exe
echo To make the installer for sharing, run share.bat

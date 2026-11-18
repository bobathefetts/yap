@echo off
setlocal
rem Yap keeps its Python environment, models and settings in %USERPROFILE%\.yap
rem (a short path: Windows cannot load DLLs from paths over 260 characters).
set "VENV=%USERPROFILE%\.yap\venv"
where uv >nul 2>nul || (echo This needs uv: https://docs.astral.sh/uv/getting-started/installation/ & exit /b 1)
uv venv "%VENV%" --managed-python --python 3.12 --allow-existing || exit /b 1
uv pip install --python "%VENV%" -r "%~dp0requirements.txt" || exit /b 1
echo.
echo Done. Start Yap with run.bat

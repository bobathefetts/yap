@echo off
rem Makes the files to send to other people, in release\ (run build.bat first):
rem   Yap-Setup.exe          small installer; Yap downloads its models on first launch
rem   Yap-Setup-Offline.zip  installer plus all models (about 4 GB); no internet needed
cd /d "%~dp0"
"%USERPROFILE%\.yap\venv\Scripts\python.exe" tools\make_release.py

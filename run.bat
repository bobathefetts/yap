@echo off
rem Starts Yap in the background (tray icon, no console window).
start "" "%USERPROFILE%\.yap\venv\Scripts\pythonw.exe" "%~dp0yap.pyw"

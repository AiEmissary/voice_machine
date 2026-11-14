@echo off
cd /d %~dp0

REM Prelaunch OBSBOT control process used by strict OBSBOT-only runtime.
if exist "C:\Program Files\OBSBOT Center\bin\OBSBOT_Center.exe" (
  start "" /D "C:\Program Files\OBSBOT Center\bin" "OBSBOT_Center.exe"
  timeout /t 4 /nobreak >nul
) else if exist "C:\Program Files\OBSBOT Center\bin\OBSBOT_Main.exe" (
  start "" /D "C:\Program Files\OBSBOT Center\bin" "OBSBOT_Main.exe"
  timeout /t 4 /nobreak >nul
)

python main.py

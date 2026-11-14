@echo off
setlocal
cd /d %~dp0
start "voice_machine" "%~dp0start_voice_machine.bat"
start "voice_back" "%~dp0voice_back\start_voice_back.bat"
endlocal

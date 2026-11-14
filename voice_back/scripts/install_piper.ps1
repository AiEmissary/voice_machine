$ErrorActionPreference = "Stop"
$root = "C:\Users\rajka\Desktop\voice_machine\voice_back"
$toolsDir = Join-Path $root "tools\piper"
$modelsDir = Join-Path $root "models\piper"
New-Item -ItemType Directory -Force -Path $toolsDir, $modelsDir | Out-Null

$piperZip = Join-Path $toolsDir "piper_windows_amd64.zip"
Invoke-WebRequest -Uri "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip" -OutFile $piperZip
Expand-Archive -Path $piperZip -DestinationPath $toolsDir -Force

$piperExe = Get-ChildItem -Path $toolsDir -Filter "piper.exe" -Recurse | Select-Object -First 1
if (-not $piperExe) { throw "piper.exe not found after extraction" }

$modelBase = "en_US-amy-low"
$modelOnnx = Join-Path $modelsDir ($modelBase + ".onnx")
$modelJson = Join-Path $modelsDir ($modelBase + ".onnx.json")

Invoke-WebRequest -Uri "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/low/en_US-amy-low.onnx" -OutFile $modelOnnx
Invoke-WebRequest -Uri "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/low/en_US-amy-low.onnx.json" -OutFile $modelJson

Write-Output "piperExe=$($piperExe.FullName)"
Write-Output "modelOnnx=$modelOnnx"
Write-Output "modelJson=$modelJson"

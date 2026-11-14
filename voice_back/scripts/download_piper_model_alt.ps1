$ErrorActionPreference = "Stop"
$root = "C:\Users\rajka\Desktop\voice_machine\voice_back"
$modelsDir = Join-Path $root "models\piper"
New-Item -ItemType Directory -Force -Path $modelsDir | Out-Null

$modelBase = "en_US-amy-low_vb"
$modelOnnx = Join-Path $modelsDir ($modelBase + ".onnx")
$modelJson = Join-Path $modelsDir ($modelBase + ".onnx.json")

Start-BitsTransfer -Source "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/low/en_US-amy-low.onnx" -Destination $modelOnnx
Invoke-WebRequest -Uri "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/low/en_US-amy-low.onnx.json" -OutFile $modelJson

Write-Output "modelOnnx=$modelOnnx"
Write-Output "modelJson=$modelJson"

$ErrorActionPreference = "Stop"
$piper = "C:\Users\rajka\Desktop\voice_machine\voice_back\tools\piper\piper\piper.exe"
$model = "C:\Users\rajka\Desktop\voice_machine\voice_back\models\piper\en_US-amy-low_vb.onnx"
$cfg = "C:\Users\rajka\Desktop\voice_machine\voice_back\models\piper\en_US-amy-low_vb.onnx.json"
$out = "C:\Users\rajka\Desktop\voice_machine\voice_back\logs\current\tts_cache\piper_test.wav"
New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
"hello from voice back" | & $piper --model $model --config $cfg --output_file $out
Get-Item $out | Select-Object FullName,Length

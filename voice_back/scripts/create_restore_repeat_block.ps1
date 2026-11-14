$ErrorActionPreference = "Stop"
$root = "C:\Users\rajka\Desktop\voice_machine\voice_back"
$stamp = Get-Date -Format "yyyyMMdd_HHmm"
$dest = Join-Path $root ("restore_points\voice_back_restore_" + $stamp + "_repeat_block.zip")
$paths = @('app','config','docs','logs','main.py','requirements.txt','README.md','start_voice_back.bat','scripts','tools','models')
$full = $paths | ForEach-Object { Join-Path $root $_ }
Compress-Archive -Path $full -DestinationPath $dest -Force
Write-Output $dest

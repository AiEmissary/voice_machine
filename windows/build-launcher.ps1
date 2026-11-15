param([string]$PythonPath,[string]$ScriptPath)
$ErrorActionPreference='Stop'
$destination=Join-Path $env:LOCALAPPDATA 'VoiceMachine'
New-Item -ItemType Directory -Path $destination -Force | Out-Null
if (!$PythonPath) {
 $candidate=Join-Path ([Environment]::GetFolderPath('Desktop')) 'Andromeda2_runtime/voice-venv-py312/Scripts/pythonw.exe'
 if (Test-Path -LiteralPath $candidate) {$PythonPath=$candidate} else {$PythonPath=(Get-Command pythonw.exe -ErrorAction Stop).Source}
}
if (!$ScriptPath) {$ScriptPath=Join-Path (Split-Path $PSScriptRoot) 'speak_v2/run.py'}
$PythonPath=(Resolve-Path -LiteralPath $PythonPath).Path
$ScriptPath=(Resolve-Path -LiteralPath $ScriptPath).Path
Add-Type -AssemblyName System.Drawing
$bitmap=New-Object System.Drawing.Bitmap 64,64
$graphics=[System.Drawing.Graphics]::FromImage($bitmap)
$graphics.SmoothingMode=[System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.Clear([System.Drawing.Color]::Transparent)
$brush=New-Object System.Drawing.Drawing2D.LinearGradientBrush ([System.Drawing.Rectangle]::new(0,0,64,64)),([System.Drawing.Color]::FromArgb(90,90,90)),([System.Drawing.Color]::FromArgb(12,12,12)),90
$graphics.FillEllipse($brush,1,1,62,62)
$font=New-Object System.Drawing.Font 'Segoe UI',34,([System.Drawing.FontStyle]::Bold),([System.Drawing.GraphicsUnit]::Pixel)
$format=New-Object System.Drawing.StringFormat
$format.Alignment=[System.Drawing.StringAlignment]::Center;$format.LineAlignment=[System.Drawing.StringAlignment]::Center
$graphics.DrawString('S',$font,[System.Drawing.Brushes]::White,([System.Drawing.RectangleF]::new(0,0,64,64)),$format)
$handle=$bitmap.GetHicon();$icon=[System.Drawing.Icon]::FromHandle($handle)
$iconPath=Join-Path $destination 'VoiceMachine.ico'
$stream=[System.IO.File]::Create($iconPath)
try{$icon.Save($stream)}finally{$stream.Dispose();$icon.Dispose();$format.Dispose();$font.Dispose();$brush.Dispose();$graphics.Dispose();$bitmap.Dispose()}
$compiler=Join-Path $env:SystemRoot 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$output=Join-Path $destination 'VoiceMachine.exe'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ /reference:System.Web.Extensions.dll "/win32icon:$iconPath" "/out:$output" (Join-Path $PSScriptRoot 'VoiceMachineLauncher.cs')
if($LASTEXITCODE -ne 0){throw 'Voice launcher compilation failed.'}
@{python=$PythonPath;script=$ScriptPath}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $destination 'launcher.json') -Encoding UTF8
$shell=New-Object -ComObject WScript.Shell
$link=$shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Programs')) 'Voice Machine.lnk'))
$link.TargetPath=$output;$link.WorkingDirectory=$destination;$link.IconLocation="$iconPath,0";$link.Description='Bring back the S voice button, or start it if it is not running.';$link.Save()
Write-Output "Built $output and the Voice Machine Start menu shortcut."

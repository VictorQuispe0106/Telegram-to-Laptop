# Downloads the latest cloudflared for Windows into <project root>/tools/.
# Run once; watchdog.py relaunches the tunnel from tools/cloudflared.exe.

# Project root = parent of the scripts directory.
$root = Split-Path -Parent $PSScriptRoot
$dest = Join-Path $root "tools"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$exe = Join-Path $dest "cloudflared.exe"
$url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"

Write-Host "Downloading cloudflared..."
Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing

if (-not (Test-Path $exe)) {
    throw "Download failed: $exe not found"
}
$size = (Get-Item $exe).Length
if ($size -le 1MB) {
    throw "Downloaded file too small ($size bytes) - likely an error page"
}
Write-Host "OK: cloudflared installed at $exe ($([math]::Round($size / 1MB, 1)) MB)"

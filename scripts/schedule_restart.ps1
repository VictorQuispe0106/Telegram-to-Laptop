# Detached delayed restart: lets the current RPC answer reach Telegram first.
Start-Sleep -Seconds 60
& "$env:LOCALAPPDATA\Microsoft\WindowsApps\python.exe" "C:\Users\E758455\Desktop\Opencode-proyects\APP TELEGRAM\scripts\restart_bot.py"

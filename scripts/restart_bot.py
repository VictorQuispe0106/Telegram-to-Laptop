"""Restart SpiderVic Assistant: kills old bot.py + old RPC shell, starts new bot.

Order matters:
1. Kill bot.py instances (old code in memory).
2. Kill RPC shell / agent session via scripts/kill_all.py (this also kills any live
   pi session served by the old shell - expected).
3. Start the new bot.py hidden (it boots a fresh RPC shell on first message).
"""
import os
import subprocess
import time

import psutil

BASE = r"C:\Users\E758455\Desktop\Opencode-proyects\APP TELEGRAM"
PY = r"C:\Users\E758455\AppData\Local\Microsoft\WindowsApps\python.exe"
LOG = BASE + r"\_restart.log"


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


log("reinicio iniciado")
killed = []
for p in psutil.process_iter(["pid", "name", "cmdline"]):
    try:
        joined = " ".join(p.info["cmdline"] or [])
        if "_restart_bot" in joined:
            continue  # never kill ourselves
        if "bot.py" in joined and p.info["name"].lower().startswith("python"):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True
            )
            killed.append(p.pid)
    except Exception:
        pass
log(f"bot.py viejos muertos: {killed}")

subprocess.run([PY, os.path.join(BASE, "scripts", "kill_all.py")],
               cwd=BASE, capture_output=True)
log("shell RPC viejo muerto")

CMD = (
    "Start-Process -WindowStyle Hidden "
    f"-FilePath '{PY}' -ArgumentList 'bot.py' "
    f"-WorkingDirectory '{BASE}'"
)
subprocess.Popen(
    ["powershell", "-NoProfile", "-Command", CMD],
    creationflags=subprocess.CREATE_NO_WINDOW,
)
log("bot nuevo lanzado - reinicio completo")

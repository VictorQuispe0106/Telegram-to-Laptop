import psutil
import subprocess
import time

VICTIMS = []
for p in psutil.process_iter(["pid", "name", "cmdline"]):
    try:
        joined = " ".join(p.info["cmdline"] or [])
        if "spidervic-telegram" in joined and p.info["name"].lower() in (
            "node.exe", "cmd.exe", "python.exe",
        ):
            # excluir nuestro propio proceso de scan
            if "-X utf8" in joined or "-c" in joined[:200]:
                if "spidervic-telegram" not in joined:
                    continue
            VICTIMS.append(p.info["pid"])
    except Exception:
        pass

print("matando:", VICTIMS)
for pid in VICTIMS:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
time.sleep(2)
print("listo")

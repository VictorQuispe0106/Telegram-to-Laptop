"""apps_control.py — Ver y cerrar aplicaciones con ventana (sin agente IA).

Lista de apps con ventana principal y cierre elegante (CloseMainWindow
primero, force solo como ultimo recurso). Lista negra de procesos
protegidos que NUNCA se cierran.
"""
import re
import subprocess

# Procesos que no se tocan ni de broma: sistema, seguridad, VPN, el propio bot
PROTEGIDOS = {
    "explorer", "dwm", "winlogon", "csrss", "services", "lsass", "svchost",
    "system", "registry", "smss", "wininit", "fontdrvhost", "sihost",
    "runtimebroker", "conhost", "dllhost", "taskhostw", "ctfmon",
    # seguridad corporativa (bloquear la sesion si mueren)
    "cyserver", "avp", "kavfs", "kavshell", "cortex", "cyxtera",
    "accessagent", "essosso", "forti",  # fortitray/fortivpn: VPN y tray
    # motor embebido de otras apps
    "msedgewebview2",
    # el propio bot y herramientas del asistente (python corre el bot!)
    "bot", "pythonw", "python", "gentle", "node", "spidervic", "pwsh", "powershell",
    # shell de windows
    "searchhost", "widgets", "textinputhost", "shellexperiencehost",
    "startmenuexperiencehost", "program", "applicationframehost",
}


def listar_apps() -> list[dict]:
    """Apps con ventana visible: [{name, title, pid, ram_mb}]."""
    ps = (
        "Get-Process | Where-Object { $_.MainWindowHandle -ne 0 } | "
        "Sort-Object WorkingSet64 -Descending | "
        "Select-Object -First 40 | ForEach-Object { "
        "'{0}|{1}|{2}|{3}' -f $_.ProcessName, $_.Id,"
        " [math]::Round($_.WorkingSet64/1MB,0), $_.MainWindowTitle } | "
        "Out-String -Width 1000"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    apps: dict[str, dict] = {}
    patron = re.compile(r"([A-Za-z0-9_.\-]+)\|(\d+)\|(\d+)\|(.*)")
    for line in out.splitlines():
        mt = patron.search(line)
        if mt and mt.group(1).lower() not in PROTEGIDOS:
            name, pid, ram, title = mt.groups()
            if name not in apps:  # dedupe por proceso (mayor RAM primero)
                apps[name] = {"name": name, "title": title, "pid": pid, "ram_mb": ram}
    return list(apps.values())


def apps_texto() -> str:
    apps = listar_apps()
    if not apps:
        return "No hay apps cerrables con ventana abierta."
    lines = ["🖥️ Apps con ventana abierta (cerrables):"]
    for a in apps:
        if not a["title"].strip() or a["name"].lower().startswith("python"):
            continue
        lines.append(f"• {a['name']} ({a['ram_mb']} MB) — {a['title'][:60]}")
    return "\n".join(lines)


def cerrar_app(nombre: str) -> str:
    """Cierra GRACIOSAMENTE todas las ventanas de un proceso (por nombre).

    Devuelve mensaje de resultado. Nunca toca PROTEGIDOS.
    """
    nombre = nombre.strip().lower()
    if nombre in PROTEGIDOS:
        return f"🛑 '{nombre}' está en la lista de procesos protegidos (sistema/seguridad/bot). No se cierra."
    if not nombre.isalnum() and nombre not in {"sissganew", "sys_corp", "msedge",
                                               "excel", "winword", "acrod32", "opera"}:
        return "🛑 Nombre de proceso inválido (solo letras/números/_)."

    ps = (
        f"Get-Process -Name '{nombre}' -ErrorAction SilentlyContinue | "
        "Where-Object { $_.MainWindowTitle -ne '' } | ForEach-Object { "
        "$r = $_.CloseMainWindow(); '{0}|{1}' -f $_.Id, $r }"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()

    if not out:
        return f"🤷 No encontré ventanas abiertas de '{nombre}'."
    cerradas = 0
    for line in out.splitlines():
        if line.split("|")[-1] == "True":
            cerradas += 1
    total = len(out.splitlines())
    if cerradas == total:
        msg = f"✅ Cerré {cerradas} ventana(s) de '{nombre}' (cierre elegante)."
    else:
        msg = (
            f"⚠️ Cerré {cerradas}/{total} ventana(s) de '{nombre}'. "
            f"Las restantes tenían trabajo sin guardar — usá /forzar_cerrar {nombre} solo si estás seguro."
        )
    return msg


def forzar_cerrar(nombre: str) -> str:
    """Stop-Process directo (pierde trabajo sin guardar). Requiere confirm previa."""
    nombre = nombre.strip().lower()
    if nombre in PROTEGIDOS:
        return f"🛑 '{nombre}' es un proceso protegido. No se fuerza el cierre."
    if not nombre.isalnum():
        return "🛑 Nombre de proceso inválido."
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Get-Process -Name '{nombre}' -ErrorAction SilentlyContinue | Stop-Process -Force"],
        capture_output=True, timeout=30,
    )
    return f"💥 Proceso '{nombre}' forzado a cerrar."

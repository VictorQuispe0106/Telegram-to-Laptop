"""watchdog.py — Centinela de la Mini App: server + tunel + boton del chat.

Corre como daemon (arranque por Startup de Windows). Cada 5 min:
1. mini_app_server.py (puerto 5577): si no responde, relanza (oculto).
2. cloudflared: si no hay proceso, relanza el quick tunnel y recupera la URL.
3. Si la URL del tunel cambio, re-registra el menu button del bot.
4. El bot de Telegram (bot.py) se avisa a si mismo: si no responde,
   lo relanza tambien (cubre caidas en crudo del proceso del bot).
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(r"C:\Users\E758455\Desktop\Opencode-proyects\APP TELEGRAM")
# Alias de WindowsApps puede quedar roto tras un reboot (0x80070002): preferir
# el shim real del gestor de Python si existe.
_PY_CANDIDATOS = (
    r"C:\Users\E758455\AppData\Local\Python\bin\python.exe",
    r"C:\Users\E758455\AppData\Local\Microsoft\WindowsApps\python.exe",
)
PY = next((p for p in _PY_CANDIDATOS if Path(p).exists()), _PY_CANDIDATOS[0])
TUNNEL_LOG = BASE / "tools" / "pinggy_out.log"
INTERVALO = 300

sys.path.insert(0, BASE.as_posix())


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(BASE / "watchdog.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def http_ok(url: str, timeout: int = 10) -> int | None:
    import requests
    try:
        r = requests.get(url, timeout=timeout)
        return r.status_code
    except Exception:
        return None


def hay_proceso(fragmento: str) -> bool:
    """Chequeo con psutil: sin spawn de PowerShell (colgaba bajo RAM alta)."""
    import psutil
    fragmento = fragmento.lower()
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if p.info["name"] and p.info["name"].lower() in ("python.exe", "cloudflared.exe"):
                cmd = " ".join(p.info["cmdline"] or []).lower()
                if fragmento in cmd:
                    return True
        except Exception:
            pass
    return False


def matar_tunel():
    """Mata zombis: ssh de pinggy vivo con tunel muerto, o cloudflared legacy."""
    import psutil
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            nombre = (p.info["name"] or "").lower()
            cmd = " ".join(p.info["cmdline"] or []).lower()
            es_pinggy = nombre == "ssh.exe" and "a.pinggy.io" in cmd
            es_cloudflared = nombre == "cloudflared.exe"
            if es_pinggy or es_cloudflared:
                p.kill()
                log(f"tunel zombi {p.pid} ({nombre}) matado")
        except Exception:
            pass


def url_del_tunel() -> str:
    """Recupera la URL del ultimo log del tunel (out y err, o '')."""
    try:
        texto = TUNNEL_LOG.read_text(encoding="utf-8", errors="ignore")
        try:
            texto += "\n" + (BASE / "tools" / "pinggy_err.log").read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass
        import re
        patrones = (
            r"https://[a-z0-9-]+\.(?:run\.pinggy-free\.link|free\.pinggy\.net|a\.pinggy\.io)",
            r"https://[a-z0-9-]+\.trycloudflare\.com",
        )
        for patron in patrones:
            urls = re.findall(patron, texto)
            if urls:
                return urls[-1]
        return ""
    except Exception:
        return ""


def url_registrada() -> str:
    try:
        return (BASE / "tools" / "tunel_url.txt").read_text().strip()
    except Exception:
        return ""


def guardar_url(url: str):
    (BASE / "tools" / "tunel_url.txt").write_text(url)


def _miniapp_chat_id() -> str:
    """Chat destino para el boton de la Mini App: MINIAPP_CHAT_ID o el primer
    ID de ALLOWED_USER_IDS (mismo parseo que bot.py). Vacio si no hay ninguno."""
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
    mid = os.getenv("MINIAPP_CHAT_ID", "").strip()
    if mid:
        return mid
    for x in os.getenv("ALLOWED_USER_IDS", "").replace(";", ",").split(","):
        if x.strip():
            return x.strip()
    return ""


def registrar_boton(url: str):
    """Re-registra el menu button + avisa al usuario."""
    chat_id = _miniapp_chat_id()
    if not chat_id:
        log("sin MINIAPP_CHAT_ID ni ALLOWED_USER_IDS -> registro de boton omitido")
        return
    try:
        import telebot
        from dotenv import load_dotenv
        load_dotenv(BASE / ".env")
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        b = telebot.TeleBot(token, parse_mode=None)
        kb = telebot.types.InlineKeyboardMarkup()
        kb.row(telebot.types.InlineKeyboardButton(
            "📋 Abrir Ocupabilidad",
            web_app=telebot.types.WebAppInfo(url=url)))
        b.set_chat_menu_button(
            chat_id=int(chat_id),
            menu_button=telebot.types.MenuButtonWebApp(
                text="📋 Ocupabilidad",
                web_app=telebot.types.WebAppInfo(url=url)))
        b.send_message(int(chat_id),
                       f"🔄 La app cambió de dirección (túnel renovado).\n"
                       f"Nueva: {url}",
                       reply_markup=kb)
        log(f"boton re-registrado con {url}")
    except Exception as e:
        log(f"error re-registrando boton: {e}")


def lanzar_server():
    """Directo desde Python con logs capturados: sin stderr invisible ya no
    comemos 500 sin rastro (incidente del 07/10 en /api/estado)."""
    try:
        out = open(BASE / "tools" / "miniapp_out.log", "w", encoding="utf-8", errors="replace")
        err = open(BASE / "tools" / "miniapp_err.log", "w", encoding="utf-8", errors="replace")
        subprocess.Popen(
            [PY, "mini_app_server.py"], cwd=str(BASE),
            stdout=out, stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=False,
        )
        log("mini_app_server relanzado")
    except Exception as e:
        log(f"error lanzando server: {e}")


def lanzar_tunel():
    """Tunel Pinggy por SSH sobre el puerto 443 (la red bloquea el 7844 de
    Cloudflare). Sesion free de 60 min: el propio ciclo la renueva.
    Se lanza DIRECTO desde Python (sin PowerShell intermediario): los logs
    quedan closetos conectados al hijo y url_del_tunel los ve siempre."""
    ssh = r"C:\Program Files\Git\usr\bin\ssh.exe"
    try:
        out = open(TUNNEL_LOG, "w", encoding="utf-8", errors="replace")
        err = open(BASE / "tools" / "pinggy_err.log", "w", encoding="utf-8", errors="replace")
        subprocess.Popen(
            [ssh, "-p", "443", "-T", "-o", "StrictHostKeyChecking=no",
             "-o", "ServerAliveInterval=30", "-R0:localhost:5577", "a.pinggy.io"],
            stdout=out, stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=False,
        )
        log("tunel pinggy relanzado")
    except Exception as e:
        log(f"error lanzando tunel: {e}")


def lanzar_bot():
    subprocess.Popen(
        ["powershell", "-NoProfile", "-Command",
         f"Start-Process -WindowStyle Hidden '{PY}' -ArgumentList 'bot.py' "
         f"-WorkingDirectory '{BASE}'"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    log("bot.py relanzado")


def ciclo():
    # 0. bot de telegram: si el proceso murio en crudo, revivirlo
    if not hay_proceso("bot.py"):
        log("bot MUERTO -> relanzando")
        lanzar_bot()
        time.sleep(10)
    # 1. server local
    if http_ok("http://127.0.0.1:5577/", timeout=8) is None:
        log("server MUERTO -> relanzando")
        lanzar_server()
        time.sleep(10)
    # 2. tunel: si la URL no responde, matar zombis y relanzar SIEMPRE
    url = url_del_tunel()
    tunel_ok = bool(url) and http_ok(f"{url}/", timeout=15) is not None
    if not tunel_ok:
        log(f"tunel sin respuesta ({url or 'sin URL'}) -> relanzando")
        matar_tunel()               # zombi: proceso vivo, tunel muerto
        lanzar_tunel()
        time.sleep(25)
        url = url_del_tunel()
    # 3. re-registrar si la url cambio
    if url and url != url_registrada():
        guardar_url(url)
        registrar_boton(url)


def main():
    # dedupe: solo un watchdog (evita instancias dobles via tarea de logon + manual,
    # que en cascada podrian lanzar 2 bot.py -> conflicto 409 de Telegram)
    import psutil
    me_pid = os.getpid()
    for p in psutil.process_iter(["name", "cmdline", "pid"]):
        try:
            if (p.info["name"] and p.info["name"].lower() == "python.exe"
                    and p.info["pid"] != me_pid
                    and "watchdog.py" in " ".join(p.info["cmdline"] or [])):
                log(f"watchdog duplicado (pid {p.info['pid']}) -> salgo sin luchar")
                return
        except Exception:
            pass
    log("watchdog iniciado")
    while True:
        try:
            ciclo()
        except Exception as e:
            log(f"error en ciclo: {e}")
        time.sleep(INTERVALO)


if __name__ == "__main__":
    main()

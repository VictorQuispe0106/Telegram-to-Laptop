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
PY = r"C:\Users\E758455\AppData\Local\Microsoft\WindowsApps\python.exe"
TUNNEL_LOG = BASE / "tools" / "tunel_err.log"
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


def matar_cloudflared():
    import psutil
    for p in psutil.process_iter(["name"]):
        try:
            if p.info["name"] and p.info["name"].lower() == "cloudflared.exe":
                p.kill()
                log(f"cloudflared zombi {p.pid} matado")
        except Exception:
            pass


def url_del_tunel() -> str:
    """Recupera la URL del ultimo log del tunel (o None)."""
    try:
        texto = TUNNEL_LOG.read_text(encoding="utf-8", errors="ignore")
        import re
        m = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", texto)
        return m[-1] if m else ""
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
    subprocess.Popen(
        ["powershell", "-NoProfile", "-Command",
         f"Start-Process -WindowStyle Hidden '{PY}' -ArgumentList 'mini_app_server.py' "
         f"-WorkingDirectory '{BASE}'"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    log("mini_app_server relanzado")


def lanzar_tunel():
    subprocess.Popen(
        ["powershell", "-NoProfile", "-Command",
         f"Start-Process -WindowStyle Hidden '{BASE / 'tools' / 'cloudflared.exe'}' "
         f"-ArgumentList 'tunnel','--url','http://localhost:5577' "
         f"-RedirectStandardError '{TUNNEL_LOG}' "
         f"-RedirectStandardOutput '{BASE / 'tools' / 'tunel_out.log'}'"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    log("cloudflared relanzado")


def ciclo():
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
        matar_cloudflared()          # zombi: proceso vivo, tunel muerto
        lanzar_tunel()
        time.sleep(15)
        url = url_del_tunel()
    # 3. re-registrar si la url cambio
    if url and url != url_registrada():
        guardar_url(url)
        registrar_boton(url)


def main():
    log("watchdog iniciado")
    while True:
        try:
            ciclo()
        except Exception as e:
            log(f"error en ciclo: {e}")
        time.sleep(INTERVALO)


if __name__ == "__main__":
    main()

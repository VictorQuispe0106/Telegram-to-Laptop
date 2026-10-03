#!/usr/bin/env python3
"""
🕷️ SpiderVic Assistant v2 — Telegram → gentle-shell RPC (proceso vivo).

Arquitectura:
    Telegram ⇄ bot.py ⇄ UN proceso `gentle-shell -- --mode rpc` persistente
    (JSONL stdin/stdout, sesión --session-id fija => contexto acumulado).

Mejoras v2 sobre v1 (--print por mensaje):
    - Proceso RPC vivo: respuestas en segundos, sin relanzar el agente por mensaje
    - Indicador "escribiendo…" en Telegram mientras el agente piensa
    - Relanzamiento automático del proceso si muere
    - Timeout con abort limpio del agente
    - Log con timestamps (bot.log)
    - /status y /top vía psutil (respuesta instantánea, sin IA)
    - /reset => new_session real del agente; /compact => compacta la sesión

Seguridad:
    - Solo responde a IDs en ALLOWED_USER_IDS (.env)
    - Un prompt a la vez (el agente no tolera dos concurrentes bien)

Run:
    python bot.py        (o pythonw.exe bot.py para que no abra consola)
"""

import ctypes
import datetime
import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import telebot.apihelper

import psutil
import requests
import telebot
from dotenv import load_dotenv, find_dotenv

# ---------- config ----------
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(find_dotenv(str(BASE_DIR / ".env")))

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_IDS = {
    x.strip()
    for x in os.getenv("ALLOWED_USER_IDS", "").replace(";", ",").split(",")
    if x.strip()
}
SESSION_ID = os.getenv("SESSION_ID", "spidervic-telegram").strip()
TIMEOUT_SECONDS = int(os.getenv("TIMEOUT_SECONDS", "300"))

TELEGRAM_MAX = 4000
START_TIME = time.time()

if not TOKEN:
    sys.exit("ERROR: TELEGRAM_BOT_TOKEN missing in .env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(BASE_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("spidervic")

bot = telebot.TeleBot(TOKEN, parse_mode=None)

# ---------- puente RPC ----------
class PiRPC:
    """Un proceso gentle-shell --mode rpc vivo, con relanzamiento automático."""

    def __init__(self):
        self.proc = None
        self.q = None
        self.req = 0

    def _cmd(self):
        return [
            "cmd", "/c", "gentle-shell",
            "--",
            "--mode", "rpc",
            "--session-id", SESSION_ID,
        ]

    def _launch(self):
        if self.proc and self.proc.poll() is None:
            # matar el ARBOL completo (cmd -> node -> pi), no solo el cmd wrapper;
            # un hijo huerfano mantiene la sesion y tumba al nuevo con EOF
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                    capture_output=True, timeout=15,
                )
            except Exception:
                pass
            time.sleep(2)  # dar tiempo a que libere el lock de sesion
        log.info("levantando proceso RPC: %s", " ".join(self._cmd()))
        self.proc = subprocess.Popen(
            self._cmd(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(BASE_DIR),
            bufsize=1,
        )
        self.q = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            for line in self.proc.stdout:
                self.q.put(line.rstrip("\n"))
        except Exception:
            pass
        self.q.put(None)  # marcador EOF

    def _alive(self):
        return self.proc is not None and self.proc.poll() is None

    def _write(self, rec: dict):
        self.proc.stdin.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def ensure(self, handshake_timeout=90) -> bool:
        """Garantiza proceso vivo + handshake get_state."""
        for attempt in (1, 2):
            if not self._alive():
                self._launch()
            try:
                resp = self._call("get_state", timeout=handshake_timeout)
                if resp and resp.get("success"):
                    return True
                log.warning("handshake falló (intento %s): %s", attempt, resp)
            except Exception as e:
                log.warning("handshake excepción (intento %s): %s", attempt, e)
        return False

    def _call(self, ctype: str, timeout=30, **fields):
        self.req += 1
        rid = f"r{self.req}"
        rec = {"id": rid, "type": ctype}
        rec.update(fields)
        self._write(rec)
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self._next_line(deadline)
            if line is None:
                # silencio de <=1s mientras bootea: seguir esperando hasta deadline
                continue
            r = json.loads(line)
            if r.get("type") == "response" and r.get("id") == rid:
                return r
        return None

    def _next_line(self, deadline):
        remaining = deadline - time.time()
        if remaining <= 0:
            return None
        try:
            line = self.q.get(timeout=min(1.0, remaining))
        except queue.Empty:
            return None  # que el caller re-evalúe su deadline
        if line is None:
            raise RuntimeError("proceso RPC murió (EOF)")
        return line

    def prompt(self, message: str, timeout: int) -> str:
        """Envía prompt, espera agent_settled, devuelve el texto final."""
        if not self._alive():
            if not self.ensure():
                raise RuntimeError("no pude levantar el proceso RPC")

        self.req += 1
        rid = f"r{self.req}"
        self._write({"id": rid, "type": "prompt", "message": message})
        log.info("prompt enviado (%s chars)", len(message))

        deadline = time.time() + timeout
        text = None
        while True:
            if time.time() > deadline:
                self._abort()
                raise TimeoutError(f"agente excedió {timeout}s — abortado")
            line = self._next_line(deadline)
            if line is None:
                continue
            r = json.loads(line)
            t = r.get("type")
            if t == "response" and r.get("id") == rid and not r.get("success"):
                raise RuntimeError("prompt rechazado: " + str(r.get("error")))
            if t == "message_end":
                msg = r.get("message", {})
                if msg.get("role") == "assistant":
                    txt = "".join(
                        b.get("text", "")
                        for b in msg.get("content", [])
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                    if txt.strip():
                        text = txt
            elif t == "agent_settled":
                if text:
                    return text
                resp = self._call("get_last_assistant_text", timeout=30)
                if resp and resp.get("success"):
                    return ((resp.get("data") or {}).get("text")) or "(sin respuesta)"
                return text or "(sin respuesta)"
            # tool events, compaction, retries… se ignoran (el log de pi va a stderr)

    def _abort(self):
        try:
            self._write({"type": "abort"})
            time.sleep(2)
        except Exception:
            pass

    def new_session(self):
        return self._call("new_session", timeout=30)

    def compact(self):
        return self._call("compact", timeout=120)


rpc = PiRPC()
rpc_lock = threading.Lock()  # un prompt a la vez

# ---------- cola de mensajes (feature v3) ----------
# Mientras el agente procesa, los mensajes nuevos entran a una cola FIFO
# y un worker los atiende en orden cuando termina el actual.
_cola: list[tuple[int, str]] = []  # (chat_id, texto)
_cola_cv = threading.Condition()


# ---------- helpers ----------
def allowed(user_id) -> bool:
    return str(user_id) in ALLOWED_IDS


def lock_workstation() -> bool:
    """Lock the workstation (same as Win+L). Returns True on success."""
    try:
        return bool(ctypes.windll.user32.LockWorkStation())
    except Exception:
        log.exception("lock_workstation fallo")
        return False


def send_long(chat_id: int, text: str):
    text = (text or "").strip() or "(vacío)"
    for i in range(0, len(text), TELEGRAM_MAX):
        if not _send_with_retries(chat_id, text[i : i + TELEGRAM_MAX]):
            log.error("chunk %s no enviado tras agotar reintentos", i)


# Reintentos ante errores de red (cortes intermitentes): esperas 2/5/10s.
# Los errores HTTP de Telegram (formato, token, etc.) NO se reintentan aquí.
SEND_RETRIES = (2, 5, 10)


def _send_with_retries(chat_id: int, text: str, **kwargs) -> bool:
    """Envía un mensaje reintentando ante errores de red (reset/timeout).

    Devuelve False solo si se agotan los reintentos; las ApiTelegramException
    (rechazo de Telegram) se propagan al caller para su fallback propio.
    """
    delays = (0,) + SEND_RETRIES
    for attempt, delay in enumerate(delays, 1):
        if delay:
            time.sleep(delay)
        try:
            bot.send_message(chat_id, text, **kwargs)
            return True
        except (requests.exceptions.RequestException, ConnectionError) as e:
            log.warning("red al enviar (intento %s/%s): %s", attempt, len(delays), e)
    return False


# ---------- MarkdownV2 (respuestas del agente con formato) ----------
MDV2_SPECIALS = set("_*[]()~`>#+-=|{}.!")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
_INLINE_RE = re.compile(r"`([^`\n]+)`")


def _esc_mdv2(s: str) -> str:
    return "".join("\\" + c if c in MDV2_SPECIALS else c for c in s)


def to_mdv2(text: str) -> str:
    """Convierte markdown del agente a MarkdownV2 de Telegram.

    Soporta: **bold**, `inline code`, bloques ``` (con lenguaje opcional).
    El texto restante se escapa; listas '-' y citas '>' quedan como texto.
    """
    parts = text.split("```")
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:  # bloque de código
            lang, nl, code = part.partition("\n")
            if not nl:  # era ```code``` sin salto
                code, lang = part, ""
            code = code.replace("\\", "\\\\").replace("`", "\\`").rstrip("\n")
            lang = lang.strip()
            out.append(f"```{lang}\n{code}\n```")
            continue
        # segmento de texto: proteger bold/inline como tokens, escapar, reinsertar
        tokens: list[str] = []

        def _stash(s: str) -> str:
            tokens.append(s)
            return f"\x00{len(tokens) - 1}\x00"

        seg = _INLINE_RE.sub(lambda m: _stash(
            "`" + m.group(1).replace("\\", "\\\\").replace("`", "\\`") + "`"
        ), part)
        seg = _BOLD_RE.sub(lambda m: _stash("*" + _esc_mdv2(m.group(1)) + "*"), seg)
        seg = _esc_mdv2(seg)
        seg = re.sub(r"\x00(\d+)\x00", lambda m: tokens[int(m.group(1))], seg)
        out.append(seg)
    return "".join(out)


def _chunk_text(text: str, limit: int = 3500) -> list[str]:
    """Parte el texto ORIGINAL por párrafos (evita cortar entidades)."""
    chunks, current = [], ""
    for para in text.split("\n\n"):
        candidate = (current + "\n\n" + para) if current else para
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(para) > limit:  # párrafo gigante: corte bruto
            chunks.append(para[:limit])
            para = para[limit:]
        current = para
    if current:
        chunks.append(current)
    return chunks or [text]


def _tablas_a_code(texto: str) -> str:
    """Telegram no tiene tablas: envuelve bloques de líneas con '|' en ```
    para que se rendericen monoespaciados y alineados. Quita la fila
    separadora |---|---|."""
    lineas = texto.split("\n")
    out, i, n = [], 0, len(lineas)
    while i < n:
        if lineas[i].lstrip().startswith("|"):
            bloque = []
            while i < n and lineas[i].lstrip().startswith("|"):
                bloque.append(lineas[i])
                i += 1
            bloque = [l for l in bloque
                      if not re.match(r"^\s*\|[\s:|-]+\|\s*$", l)]
            if len(bloque) >= 2:
                out.append("```")
                out.extend(bloque)
                out.append("```")
            else:
                out.extend(bloque)
        else:
            out.append(lineas[i])
            i += 1
    return "\n".join(out)


def send_long_md(chat_id: int, text: str):
    """Envía con MarkdownV2; si Telegram rechaza un chunk, lo reenvía plano.

    Errores de red: reintentos con backoff (2/5/10s) antes de rendirse,
    y el fallback plano reintenta de nuevo (hasta 6 intentos por chunk).
    """
    text = _tablas_a_code((text or "").strip()) or "(vacío)"
    for chunk in _chunk_text(text):
        md = to_mdv2(chunk)
        try:
            if _send_with_retries(chat_id, md, parse_mode="MarkdownV2"):
                continue
        except telebot.apihelper.ApiTelegramException:
            pass  # formato rechazado por Telegram: caer al plano
        if not _send_with_retries(chat_id, chunk):
            log.error("no pude enviar chunk (%s chars)", len(chunk))


def with_typing(chat_id: int, stop: threading.Event):
    """Indicador 'escribiendo…' cada 4s hasta stop."""
    while not stop.wait(4):
        try:
            bot.send_chat_action(chat_id, "typing")
        except Exception:
            pass


def ask_agent(message: str) -> str:
    """Con el lock tomado: prompt con reintentos por proceso muerto."""
    for attempt in (1, 2):
        try:
            return rpc.prompt(message, timeout=TIMEOUT_SECONDS)
        except RuntimeError as e:
            log.error("RPC error (intento %s): %s — relanzo", attempt, e)
            rpc._launch()
        except TimeoutError:
            raise
        except Exception as e:
            log.error("excepción inesperada (intento %s): %s", attempt, e)
            rpc._launch()
    raise RuntimeError("RPC falló tras 2 intentos")


# ---------- métricas (rápidas, sin IA) ----------
def status_texto_tarjeta() -> str:
    """Tarjeta visual con barras de progreso (HTML parse mode)."""
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\")
    bat = psutil.sensors_battery()
    up = time.time() - psutil.boot_time()

    def barra(pct: float, largo: int = 10) -> str:
        llenos = max(0, min(largo, round(pct / 100 * largo)))
        return "▰" * llenos + "▱" * (largo - llenos)

    def pct_ok(p: float) -> str:
        if p < 60:
            return ""
        if p < 80:
            return " 🟡"
        return " 🔴"

    def esc_html(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    lines = [
        "📊 <b>ESTADO DE LA LAPTOP</b>",
        "━━━━━━━━━━━━━━━━━",
        f"🖥️ CPU      <code>{barra(cpu)} {cpu:.0f}%</code>{pct_ok(cpu)}",
        f"🧠 RAM      <code>{barra(mem.percent)} {mem.percent:.0f}%</code> ({mem.available // (1024**3)}GB libres){pct_ok(mem.percent)}",
        f"💾 Disco C: <code>{barra(disk.percent)} {disk.percent:.0f}%</code> ({disk.free // (1024**3)}GB libres){pct_ok(disk.percent)}",
    ]
    if bat:
        icono = "🔌" if bat.power_plugged else "🔋"
        lines.append(f"{icono} Batería   <code>{barra(bat.percent)} {bat.percent:.0f}%</code>")
    lines.append(f"⏱️ Encendida {int(up // 3600)}h {int(up % 3600 // 60)}m")
    return "\n".join(lines)


def top_text(n=8) -> str:
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            procs.append((p.info["memory_info"].rss, p.info["pid"], p.info["name"]))
        except Exception:
            continue
    procs.sort(reverse=True)
    lines = ["🔝 Top procesos por RAM:"]
    for rss, pid, name in procs[:n]:
        lines.append(f"• {name} ({pid}) — {rss // (1024*1024)}MB")
    return "\n".join(lines)


# ---------- handlers ----------
HELP_TEXT = (
    "🕷️ SpiderVic Assistant v4\n\n"
    "Escribime lo que quieras: lo procesa el agente con contexto de sesión.\n"
    "Si estoy ocupado, tu mensaje queda en cola y te respondo en orden.\n\n"
    "⚡ Instantáneos (sin IA):\n"
    "/status — métricas de la laptop\n"
    "/top — procesos que más RAM comen\n"
    "/apps — apps con ventana abierta\n"
    "/foto — captura de pantalla al chat\n"
    "/vpn — estado de la VPN (+ conectar/desconectar)\n"
    "/bloquear — bloquea la laptop (Win+L remoto)\n"
    "/limpiar — limpieza del disco C: (con preview y confirmación)\n"
    "/traer <texto> — manda un archivo de la laptop\n"
    "/forzar_cerrar <proceso> — force-kill (peligroso)\n\n"
    "🧠 Con agente: /estado, /reset, /compact, /id\n\n"
    "💡 /start muestra la botonera con todo esto."
)


def quick_actions_markup():
    """Botones de acción rápida (inline keyboard) — panel v4."""
    kb = telebot.types.InlineKeyboardMarkup()
    kb.row(telebot.types.InlineKeyboardButton("🔒 Bloquear", callback_data="lock"),
           telebot.types.InlineKeyboardButton("📸 Foto", callback_data="shot"))
    kb.row(telebot.types.InlineKeyboardButton("📊 Estado", callback_data="status"),
           telebot.types.InlineKeyboardButton("🔝 Top RAM", callback_data="top"),
           telebot.types.InlineKeyboardButton("🖥️ Apps", callback_data="apps"))
    kb.row(telebot.types.InlineKeyboardButton("🔗 VPN", callback_data="vpn"),
           telebot.types.InlineKeyboardButton("🧹 Disco", callback_data="disk"))
    kb.row(telebot.types.InlineKeyboardButton("🔊 +", callback_data="volup"),
           telebot.types.InlineKeyboardButton("🔇 Mute", callback_data="volmute"),
           telebot.types.InlineKeyboardButton("🔉 −", callback_data="voldown"))
    return kb


@bot.message_handler(commands=["start", "help"])
def cmd_start(m):
    _chats_conocidos.add(m.chat.id)
    # panel con botonera inline
    bot.reply_to(m, HELP_TEXT, reply_markup=quick_actions_markup())
    # barra persistente de botones (arriba del campo de texto)
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📊 Status", "📸 Foto", "🖥️ Apps")
    kb.row("📶 VPN", "🧹 Disco", "🔒 Bloquear")
    bot.send_message(m.chat.id, "⚡ Barra rápida activada:", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: True)
def on_callback(c):
    """Router central de botones rápidos. Operaciones largas van a thread."""
    if not allowed(c.from_user.id):
        bot.answer_callback_query(c.id, "🔒 No autorizado")
        return
    data = c.data or ""
    _chats_conocidos.add(c.message.chat.id)

    # ---- accion diferida / confirmaciones ----
    if data.startswith("confirm:"):
        accion = data[len("confirm:"):]
        pend = _pendientes.get(c.message.chat.id)
        _pendientes.pop(c.message.chat.id, None)
        if pend != accion:
            bot.answer_callback_query(c.id, "⌛ Sin acción pendiente (o vencida)")
            return
        try:
            bot.edit_message_text("▶️ Ejecutando…", c.message.chat.id, c.message.message_id)
        except Exception:
            pass
        threading.Thread(target=_ejecutar_confirmado, args=(c.message.chat.id, accion), daemon=True).start()
        bot.answer_callback_query(c.id)
        return
    if data == "cancel":
        _pendientes.pop(c.message.chat.id, None)
        try:
            bot.edit_message_text("🚫 Cancelado.", c.message.chat.id, c.message.message_id)
        except Exception:
            pass
        bot.answer_callback_query(c.id, "Cancelado")
        return

    # ---- instantáneas (<5s, sin agente) ----
    if data == "lock":
        ok = lock_workstation()
        bot.answer_callback_query(c.id, "🔒 Laptop bloqueada" if ok else "❌ No pude bloquear")
        if ok:
            try:
                bot.edit_message_text(
                    "🔒 Laptop bloqueada. Hasta ya.",
                    c.message.chat.id, c.message.message_id,
                )
            except Exception:
                pass
    elif data == "status":
        bot.answer_callback_query(c.id)
        bot.send_message(c.message.chat.id, status_texto_tarjeta(), parse_mode="HTML")
    elif data == "top":
        bot.answer_callback_query(c.id)
        bot.reply_to(c.message, top_text())
    elif data == "apps":
        bot.answer_callback_query(c.id)
        bot.reply_to(c.message, apps_control.apps_texto())
    elif data == "vpn":
        bot.answer_callback_query(c.id)
        estado = "✅ UP" if forti_vpn.vpn_up() else "⛔ DOWN"
        bot.reply_to(c.message, f"📶 VPN {forti_vpn.TUNNEL}: {estado}", reply_markup=vpn_markup())
    elif data == "disk":
        bot.answer_callback_query(c.id)
        bot.send_message(c.message.chat.id, disk_clean.preview(), reply_markup=confirmar_markup("diskclean"))
    elif data == "shot":
        bot.answer_callback_query(c.id, "📸 Capturando…")
        try:
            ruta = quick_actions.screenshot()
            with open(ruta, "rb") as f:
                bot.send_photo(c.message.chat.id, f, caption="📸 Pantalla de la laptop")
            Path(ruta).unlink(missing_ok=True)
        except Exception as e:
            log.error("shot: %s", e)
            bot.reply_to(c.message, f"❌ No pude capturar: {e}")
    elif data in ("volup", "voldown", "volmute"):
        if data == "volup":
            quick_actions.volume_up()
            msg = "🔊 Volumen +"
        elif data == "voldown":
            quick_actions.volume_down()
            msg = "🔉 Volumen −"
        else:
            quick_actions.volume_mute()
            msg = "🔇 Mute alternado"
        bot.answer_callback_query(c.id, msg)
    elif data == "clip":
        bot.answer_callback_query(c.id)
        try:
            txt = quick_actions.clipboard_text() or "(portapapeles vacío o no es texto)"
            send_long(c.message.chat.id, f"📋 Portapapeles:\n{txt[:3000]}")
        except Exception as e:
            bot.reply_to(c.message, f"❌ Portapapeles: {e}")
    elif data == "screenoff":
        quick_actions.screen_off()
        bot.answer_callback_query(c.id, "💤 Pantalla apagada")
    elif data == "suspend":
        bot.answer_callback_query(c.id)
        pedir_confirmacion(c.message.chat.id, "suspend",
                           "💤 ¿Suspendo la laptop? Podés despertarla después con el botón de encendido.")
    elif data == "vpnconnect":
        bot.answer_callback_query(c.id)
        _vpn_conectar_async(c.message.chat.id)
    elif data == "vpndisconnect":
        bot.answer_callback_query(c.id, "❌ Desconectando…")
        threading.Thread(target=_vpn_desconectar_async, args=(c.message.chat.id,), daemon=True).start()
    elif data.startswith("close:"):
        nombre = data[len("close:"):]
        bot.answer_callback_query(c.id)
        pedir_confirmacion(c.message.chat.id, f"close:{nombre}",
                           f"🖥️ ¿Cierro la app '{nombre}'? Si tiene trabajo sin guardar, lo perdés.")
    else:
        bot.answer_callback_query(c.id, "🤷 Botón desconocido")


def rpc_estado_texto() -> str:
    up = time.time() - START_TIME
    lines = [
        "🤖 Estado del asistente",
        f"• Bot vivo hace: {int(up // 60)}min {int(up % 60)}s",
        f"• Sesión: {SESSION_ID}",
        f"• Cola: {len(_cola)} mensaje(s)",
        f"• Timeout por prompt: {TIMEOUT_SECONDS}s",
    ]
    if rpc._alive():
        lines.append("• Proceso RPC: ✅ vivo")
        if rpc_lock.acquire(blocking=False):
            rpc_lock.release()
            try:
                resp = rpc._call("get_state", timeout=15)
                data = (resp or {}).get("data") or {}
                model = data.get("model") or {}
                nombre = (model.get("name") or model.get("model_id")
                          or model.get("id") or "?")
                lines.append(f"• Modelo: {nombre}")
                lines.append(f"• Mensajes en sesión: {data.get('messageCount', '?')}")
                if data.get("isStreaming"):
                    lines.append("• Agente: 🧠 pensando")
            except Exception as e:
                lines.append(f"• get_state falló: {e}")
        else:
            lines.append("• Agente: 🧠 procesando un mensaje")
    else:
        lines.append("• Proceso RPC: 😴 apagado (se prende al primer mensaje)")
    return "\n".join(lines)


@bot.message_handler(commands=["estado"])
def cmd_estado(m):
    if not allowed(m.from_user.id):
        return
    bot.reply_to(m, rpc_estado_texto())


@bot.message_handler(commands=["bloquear"])
def cmd_bloquear(m):
    if not allowed(m.from_user.id):
        return
    if lock_workstation():
        bot.reply_to(m, "🔒 Laptop bloqueada.")
        reaccionar(m.chat.id, m.message_id, "🔥")
    else:
        bot.reply_to(m, "❌ No pude bloquear la laptop.")


@bot.message_handler(commands=["id"])
def cmd_id(m):
    bot.reply_to(m, f"Tu user ID de Telegram es: {m.from_user.id}")


@bot.message_handler(commands=["status"])
def cmd_status(m):
    if not allowed(m.from_user.id):
        return
    bot.send_message(m.chat.id, status_texto_tarjeta(), parse_mode="HTML")
    reaccionar(m.chat.id, m.message_id, "🔥")


@bot.message_handler(commands=["top"])
def cmd_top(m):
    if not allowed(m.from_user.id):
        return
    bot.reply_to(m, top_text())


@bot.message_handler(commands=["reset"])
def cmd_reset(m):
    if not allowed(m.from_user.id):
        return
    if not rpc_lock.acquire(blocking=False):
        bot.reply_to(m, "⏳ Estoy procesando otra cosa, probá en un rato.")
        return
    try:
        if not rpc._alive():
            rpc.ensure()
        resp = rpc.new_session()
        ok = bool(resp and resp.get("success"))
        bot.reply_to(m, "♻️ Sesión nueva creada." if ok else "♻️ Sesión reiniciada (con aviso).")
    except Exception as e:
        log.error("reset: %s", e)
        bot.reply_to(m, f"❌ No pude reiniciar la sesión: {e}")
    finally:
        rpc_lock.release()


@bot.message_handler(commands=["compact"])
def cmd_compact(m):
    if not allowed(m.from_user.id):
        return
    if not rpc_lock.acquire(blocking=False):
        bot.reply_to(m, "⏳ Estoy procesando otra cosa, probá en un rato.")
        return
    stop = threading.Event()
    threading.Thread(target=with_typing, args=(m.chat.id, stop), daemon=True).start()
    try:
        if not rpc._alive():
            rpc.ensure()
        resp = rpc.compact()
        ok = bool(resp and resp.get("success"))
        bot.reply_to(m, "🗜️ Sesión compactada." if ok else "⚠️ Compactación cancelada o falló.")
    except Exception as e:
        log.error("compact: %s", e)
        bot.reply_to(m, f"❌ Error al compactar: {e}")
    finally:
        stop.set()
        rpc_lock.release()


def _procesar_y_responder(chat_id: int, texto: str):
    """Toma el lock, consulta al agente y envía la respuesta (con typing)."""
    if not rpc_lock.acquire(blocking=False):
        # perdimos la carrera por el lock: volver a la cola (al frente)
        with _cola_cv:
            _cola.insert(0, (chat_id, texto))
        time.sleep(1)
        return
    stop = threading.Event()
    threading.Thread(target=with_typing, args=(chat_id, stop), daemon=True).start()
    t0 = time.time()
    try:
        answer = ask_agent(texto)
        log.info("respuesta en %.1fs (%s chars)", time.time() - t0, len(answer))
    except TimeoutError as e:
        answer = f"⏱ {e}"
    except Exception as e:
        log.error("ask_agent: %s", e)
        answer = f"❌ Error hablando con el agente: {e}"
    finally:
        stop.set()
        rpc_lock.release()
    send_long_md(chat_id, answer)


def queue_worker():
    """Drena la cola FIFO: un mensaje por vez, en orden de llegada."""
    while True:
        with _cola_cv:
            while not _cola:
                _cola_cv.wait()
            chat_id, texto = _cola.pop(0)
        _procesar_y_responder(chat_id, texto)


def encolar(chat_id: int, texto: str) -> int:
    """Encola y devuelve la posición (1-based)."""
    with _cola_cv:
        _cola.append((chat_id, texto))
        return len(_cola)


@bot.message_handler(func=lambda m: True, content_types=["text"])
def on_text(m):
    uid = m.from_user.id
    if not allowed(uid):
        bot.reply_to(
            m,
            "🔒 Bot bloqueado: no estás en ALLOWED_USER_IDS.\n"
            f"Tu user ID es: {uid}",
        )
        return

    # botones de la barra persistente -> rutean a comandos/acciones rapidas
    mapa_barra = {
        "📊 Status": lambda: bot.send_message(m.chat.id, status_texto_tarjeta(), parse_mode="HTML"),
        "📸 Foto": lambda: cmd_foto(m),
        "🖥️ Apps": lambda: bot.reply_to(m, apps_control.apps_texto()),
        "📶 VPN": lambda: cmd_vpn(m),
        "🧹 Disco": lambda: bot.send_message(m.chat.id, disk_clean.preview(), reply_markup=confirmar_markup("diskclean")),
        "🔒 Bloquear": lambda: cmd_bloquear(m),
    }
    if m.text in mapa_barra:
        _chats_conocidos.add(m.chat.id)
        mapa_barra[m.text]()
        return

    if rpc_lock.acquire(blocking=False):
        rpc_lock.release()
        _procesar_y_responder(m.chat.id, m.text)
        return

    pos = encolar(m.chat.id, m.text)
    bot.reply_to(m, f"🧵 Recibido — quedó en cola (posición {pos}). Te respondo en orden.")


# ==================== TANDA 2: panel de control remoto ====================
# Helpers + comandos + gate de confirmacion + alertas background.
import apps_control
import disk_clean
import forti_vpn
import net_scan

# Nombres amistosos para dispositivos de la red (persistente)
_NOMBRES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "net_nombres.json")


def _cargar_nombres():
    try:
        with open(_NOMBRES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _guardar_nombres():
    with open(_NOMBRES_FILE, "w", encoding="utf-8") as f:
        json.dump(_NOMBRES_RED, f, indent=2, ensure_ascii=False)


_NOMBRES_RED = _cargar_nombres()
import quick_actions

_chats_conocidos: set[int] = set()
_pendientes: dict[int, str] = {}  # chat_id -> accion esperando confirm
_vpn_lock = threading.Lock()


def vpn_markup():
    kb = telebot.types.InlineKeyboardMarkup()
    kb.row(telebot.types.InlineKeyboardButton("🔗 Conectar", callback_data="vpnconnect"),
           telebot.types.InlineKeyboardButton("❌ Desconectar", callback_data="vpndisconnect"))
    kb.row(telebot.types.InlineKeyboardButton("📶 Actualizar estado", callback_data="vpn"))
    return kb


def confirmar_markup(accion: str):
    kb = telebot.types.InlineKeyboardMarkup()
    kb.row(telebot.types.InlineKeyboardButton("✅ Confirmar", callback_data=f"confirm:{accion}"),
           telebot.types.InlineKeyboardButton("❌ Cancelar", callback_data="cancel"))
    return kb


def pedir_confirmacion(chat_id: int, accion: str, texto: str):
    _pendientes[chat_id] = accion
    bot.send_message(chat_id, texto, reply_markup=confirmar_markup(accion))


def reaccionar(chat_id: int, message_id: int, emoji: str = "🔥"):
    """Reacción automática del bot (silent fail: si el tipo no soporta, no pasa nada)."""
    try:
        bot.set_message_reaction(
            chat_id, message_id,
            reaction=[telebot.types.ReactionTypeEmoji(emoji)],
            is_big=False,
        )
    except Exception:
        pass


def _ejecutar_confirmado(chat_id: int, accion: str):
    """Ejecuta la accion ya confirmada (thread aparte)."""
    try:
        if accion == "diskclean":
            bot.send_message(chat_id, disk_clean.limpiar())
        elif accion == "suspend":
            quick_actions.suspend()
            bot.send_message(chat_id, "💤 Suspendiendo (despertala con el botón de encendido).")
        elif accion == "apagar":
            bot.send_message(chat_id, "⏻ Apagando en 10 segundos. Chau!")
            quick_actions.shutdown("apagar")
        elif accion == "reiniciar":
            bot.send_message(chat_id, "🔄 Reiniciando en 10 segundos.")
            quick_actions.shutdown("reiniciar")
        elif accion.startswith("close:"):
            nombre = accion[len("close:"):]
            bot.send_message(chat_id, apps_control.cerrar_app(nombre))
        else:
            bot.send_message(chat_id, "🤷 Acción no reconocida.")
    except Exception as e:
        log.error("confirmado %s: %s", accion, e)
        bot.send_message(chat_id, f"❌ Falló: {e}")


def _vpn_conectar_async(chat_id: int):
    """Dispara el flujo SAML y hace polling del adaptador (thread)."""
    if not _vpn_lock.acquire(blocking=False):
        bot.send_message(chat_id, "⏳ Ya hay una operación de VPN en curso.")
        return
    try:
        if forti_vpn.vpn_up():
            bot.send_message(chat_id, "✅ La VPN ya está conectada.", reply_markup=vpn_markup())
            return
        bot.send_message(
            chat_id,
            "🔗 Abriendo el flujo de conexión…\n"
            "⚠️ Se va a abrir el browser con el login HITSS (Microsoft) en la laptop: "
            "completá el SSO ahí (y aprobá el MFA si te lo pide). "
            "Te confirmo cuando el túnel levante.",
        )
        ok = forti_vpn.connect(forti_vpn.TUNNEL, wait_s=180)
        if ok:
            bot.send_message(chat_id, "✅ VPN conectada.", reply_markup=vpn_markup())
        else:
            bot.send_message(
                chat_id,
                "⏱ La VPN no levantó en 3 minutos. ¿Completaste el SSO en la laptop? "
                "Probá de nuevo cuando quieras.",
                reply_markup=vpn_markup(),
            )
    except Exception as e:
        log.error("vpnconnect: %s", e)
        bot.send_message(chat_id, f"❌ Error conectando VPN: {e}")
    finally:
        _vpn_lock.release()


def _vpn_desconectar_async(chat_id: int):
    if not _vpn_lock.acquire(blocking=False):
        bot.send_message(chat_id, "⏳ Ya hay una operación de VPN en curso.")
        return
    try:
        if forti_vpn.disconnect(forti_vpn.TUNNEL, wait_s=45):
            bot.send_message(chat_id, "❌ VPN desconectada.", reply_markup=vpn_markup())
        else:
            bot.send_message(chat_id, "⚠️ No pude desconectar (¿menú ocupado?). Probá de nuevo.", reply_markup=vpn_markup())
    except Exception as e:
        log.error("vpndisconnect: %s", e)
        bot.send_message(chat_id, f"❌ Error: {e}")
    finally:
        _vpn_lock.release()


# ---------- comandos Tanda 2 ----------

@bot.message_handler(commands=["foto"])
def cmd_foto(m):
    if not allowed(m.from_user.id):
        return
    bot.reply_to(m, "📸 Capturando…")
    try:
        ruta = quick_actions.screenshot()
        with open(ruta, "rb") as f:
            bot.send_photo(m.chat.id, f, caption="📸 Pantalla de la laptop")
        Path(ruta).unlink(missing_ok=True)
        reaccionar(m.chat.id, m.message_id, "⚡")
    except Exception as e:
        bot.reply_to(m, f"❌ {e}")


@bot.message_handler(commands=["apps"])
def cmd_apps(m):
    if not allowed(m.from_user.id):
        return
    _chats_conocidos.add(m.chat.id)
    bot.reply_to(m, apps_control.apps_texto())


@bot.message_handler(commands=["forzar_cerrar"])
def cmd_forzar_cerrar(m):
    if not allowed(m.from_user.id):
        return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(m, "Uso: /forzar_cerrar <proceso> — fuerza el cierre (pierde trabajo sin guardar).")
        return
    bot.reply_to(m, apps_control.forzar_cerrar(parts[1]))


@bot.message_handler(commands=["limpiar"])
def cmd_limpiar(m):
    if not allowed(m.from_user.id):
        return
    _chats_conocidos.add(m.chat.id)
    bot.send_message(m.chat.id, disk_clean.preview(), reply_markup=confirmar_markup("diskclean"))


@bot.message_handler(commands=["vpn"])
def cmd_vpn(m):
    if not allowed(m.from_user.id):
        return
    _chats_conocidos.add(m.chat.id)
    estado = "✅ UP" if forti_vpn.vpn_up() else "⛔ DOWN"
    bot.reply_to(m, f"📶 VPN {forti_vpn.TUNNEL}: {estado}", reply_markup=vpn_markup())


@bot.message_handler(commands=["quienhay"])
def cmd_quienhay(m):
    if not allowed(m.from_user.id):
        return
    _chats_conocidos.add(m.chat.id)
    aviso = bot.reply_to(m, "📡 Escaneando la red… (10s)")
    def _trabajo():
        try:
            r = net_scan.escanear()
            if "error" in r:
                bot.edit_message_text(f"❌ {r['error']}", m.chat.id, aviso.message_id)
                return
            nuevos = [d for d in r["dispositivos"] if d["nuevo"]]
            lineas = [f"📡 Red {r['subred']} — {r['total']} dispositivos ({r['segundos']}s)", ""]
            for d in r["dispositivos"]:
                tag = "🆕" if d["nuevo"] else "▫️"
                host = f" — {d['hostname']}" if d["hostname"] else ""
                nombre = _NOMBRES_RED.get(d["mac"], "")
                lineas.append(f"{tag} {d['ip']}{host}" + (f" ({nombre})" if nombre else ""))
            lineas.append("")
            lineas.append(f"🆕 nuevos: {len(nuevos)}" if nuevos else "🔒 sin dispositivos nuevos")
            lineas.append("Tip: /nombra <mac parcial> <nombre> para etiquetar")
            bot.edit_message_text("\n".join(lineas), m.chat.id, aviso.message_id)
        except Exception as e:
            bot.edit_message_text(f"❌ Error: {e}", m.chat.id, aviso.message_id)
    threading.Thread(target=_trabajo, daemon=True).start()


@bot.message_handler(commands=["nombra"])
def cmd_nombra(m):
    if not allowed(m.from_user.id):
        return
    parts = (m.text or "").split(maxsplit=2)
    if len(parts) < 3:
        bot.reply_to(m, "Uso: /nombra <mac parcial> <nombre>\nEj: /nombra 5b:97 Router")
        return
    frag, nombre = parts[1].lower().replace("-", ":"), parts[2]
    conocidos = net_scan.cargar_conocidos()
    hits = [mac for mac in list(conocidos) + list(_NOMBRES_RED) if frag in mac]
    if not hits:
        bot.reply_to(m, f"❌ No encuentro MAC con '{frag}' (corré /quienhay primero)")
        return
    for mac in hits:
        _NOMBRES_RED[mac] = nombre
    _guardar_nombres()
    bot.reply_to(m, f"✅ Etiquetado: {', '.join(hits)} → {nombre}")


@bot.message_handler(commands=["traer"])
def cmd_traer(m):
    if not allowed(m.from_user.id):
        return
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(m, "📂 Uso: /traer <parte del nombre de archivo>\nBusco en Descargas, Escritorio y Documentos.")
        return
    patron = re.sub(r"[^\w\s\.\*\-]", "", parts[1].strip()).strip()
    if not patron:
        bot.reply_to(m, "⚠️ Caracteres no válidos. Usá solo letras, números, punto, guión o asterisco.")
        return
    ps = (
        "$dirs = @((Join-Path $env:USERPROFILE 'Downloads'),"
        " (Join-Path $env:USERPROFILE 'Desktop'),"
        " (Join-Path $env:USERPROFILE 'Documents'));"
        f"Get-ChildItem $dirs -Recurse -Filter '*{patron}*' -File -ErrorAction SilentlyContinue | "
        "Sort-Object LastWriteTime -Descending | Select-Object -First 5 FullName, Length | "
        "ForEach-Object { '{0}|{1}' -f $_.FullName, $_.Length }"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True, timeout=60,
    ).stdout.strip()
    if not out:
        bot.reply_to(m, f"🤷 Encontré nada con '{patron}' en Descargas/Escritorio/Documentos.")
        return
    lines = out.splitlines()
    if len(lines) > 1:
        lista = "\n".join(f"• {l.split('|')[0]}" for l in lines)
        bot.reply_to(m, f"Encontré {len(lines)} — refinar con más texto:\n{lista}")
        return
    ruta, size = lines[0].rsplit("|", 1)
    try:
        size_b = int(size)
    except ValueError:
        size_b = 0
    if size_b > 47 * 1024 * 1024:
        bot.reply_to(m, f"🛑 El archivo pesa {size_b/1024/1024:.0f}MB y el límite de Telegram es 50MB.")
        return
    try:
        bot.reply_to(m, "📤 Enviando…")
        with open(ruta, "rb") as f:
            bot.send_document(m.chat.id, f, visible_file_name=Path(ruta).name)
    except Exception as e:
        bot.reply_to(m, f"❌ No pude enviar el archivo: {e}")


# ---------- alertas background ----------
_alertas_ultimo: dict[str, float] = {}


def _alertas_worker():
    """Cada 5 min: disco C: <2GB (aviso cada 6h) y RAM >95% (cada 1h)."""
    while True:
        try:
            ahora = time.time()
            disk = psutil.disk_usage("C:\\")
            if disk.free < 2 * 1024 ** 3 and ahora - _alertas_ultimo.get("disk", 0) > 6 * 3600:
                _alertas_ultimo["disk"] = ahora
                _enviar_alerta(f"🚨 Disco C: casi lleno ({disk.free // (1024**3)}GB libres). Mandá 🧹 Disco.")
            mem = psutil.virtual_memory()
            if mem.percent > 95 and ahora - _alertas_ultimo.get("ram", 0) > 3600:
                _alertas_ultimo["ram"] = ahora
                _enviar_alerta(f"🚨 RAM al {mem.percent:.0f}% — mandá /top para ver quién.")
        except Exception:
            log.exception("alertas_worker")
        time.sleep(300)


def _enviar_alerta(texto: str, silenciosa: bool = False):
    """Avisa a los chats conocidos. En horario nocturno (23-7) llega sin sonido."""
    hora = datetime.datetime.now().hour
    silenciosa = silenciosa or (hora >= 23 or hora < 7)
    for chat_id in list(_chats_conocidos):
        try:
            bot.send_message(chat_id, texto, disable_notification=silenciosa)
        except Exception:
            pass


if __name__ == "__main__":
    log.info("🕷️ SpiderVic Assistant v4 online — sesión %s | whitelist %s",
             SESSION_ID, sorted(ALLOWED_IDS) or "VACÍA")
    me = bot.get_me()
    log.info("bot: @%s (id %s)", me.username, me.id)
    threading.Thread(target=queue_worker, daemon=True).start()
    threading.Thread(target=_alertas_worker, daemon=True).start()
    bot.infinity_polling(timeout=60, long_polling_timeout=60)

"""Pruebas offline de las mejoras v3 del bot (sin red, sin Telegram)."""
import os
import sys
import threading
import time

# Resolve the project root as the parent of the tests/ directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402  (import seguro: TeleBot no hace red al construirse)

failures = []


def check(name, cond, extra=""):
    mark = "OK " if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" -> {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


# ---------- 1) conversor MarkdownV2 ----------
md = bot.to_mdv2

# texto plano con especiales
r = md("Hola. Precio: 10+5=15 (ok) #tag!")
exp = r"Hola\. Precio: 10\+5\=15 \(ok\) \#tag\!"
check("escapes de texto plano", r == exp, repr(r))

# bold: el token **x** se convierte a *x* con el contenido intacto
r = md("esto es **importante** hoy")
check("bold ** -> *", r == "esto es *importante* hoy", repr(r))

# inline code conserva backticks sin escapar el resto
r = md("usa `pip install x` luego")
check("inline code", "`pip install x`" in r, repr(r))

# bloque de codigo con lenguaje
r = md("mira:\n```python\nprint('hi')\n```\nfin")
check("bloque con lenguaje", "```python\nprint('hi')\n```" in r, repr(r))

# bloque con backtick dentro
r = md("```\ncon ` backtick\n```")
check("backtick en bloque", "con \\` backtick" in r, repr(r))

# chunking por parrafos no rompe bloques
texto = "p1\n\n```py\ncodigo\n```\n\np3"
chunks = bot._chunk_text(texto)
check("chunking conserva contenido", "".join(chunks).replace("\n\n", "\n").replace("\n", "")
      == texto.replace("\n\n", "\n").replace("\n", ""), str(chunks))

# ---------- 2) cola FIFO: orden de atencion ----------
respuestas = []


def fake_ask(msg):
    time.sleep(0.05)  # simular trabajo
    return f"respuesta:{msg}"


def fake_send(chat_id, text):
    respuestas.append((chat_id, text))


bot.ask_agent = fake_ask
bot.send_long_md = fake_send
bot.with_typing = lambda *a, **k: None

for i, chat in enumerate([(111, f"m{i}") for i in range(3)]):
    pos = bot.encolar(*chat)
    assert pos == i + 1

w = threading.Thread(target=bot.queue_worker, daemon=True)
w.start()
deadline = time.time() + 10
while len(respuestas) < 3 and time.time() < deadline:
    time.sleep(0.05)

check("3 mensajes encolados respondidos en orden",
      [t for _, t in respuestas] == ["respuesta:m0", "respuesta:m1", "respuesta:m2"],
      str(respuestas))
check("posicion informada correcta", True)

# ---------- 3) /estado usa get_state defensivamente ----------
txt = bot.rpc_estado_texto()
check("/estado genera texto sin explotar (rpc apagado)", "Estado del asistente" in txt)
print("--- /estado ---")
print(txt)

print()
print("FALLOS:", failures if failures else "ninguno ✅")
sys.exit(1 if failures else 0)

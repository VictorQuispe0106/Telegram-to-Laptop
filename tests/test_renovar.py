"""Pruebas offline de la renovación manual del túnel (sin red, sin SSH).

Ejecutar: python tests/test_renovar.py
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import watchdog  # noqa: E402  (import seguro: main() está bajo __main__)
import bot  # noqa: E402  (import seguro: TeleBot no hace red al construirse)

failures = []


def check(name, cond, extra=""):
    mark = "OK " if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" -> {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


# ---------- 1) el ciclo automático ya NO renueva el túnel ----------
ciclo_src = inspect.getsource(watchdog.ciclo)
check("ciclo no relanza el túnel", "lanzar_tunel" not in ciclo_src)
check("ciclo no mata zombis del túnel", "matar_tunel" not in ciclo_src)
check("ciclo no re-registra el botón", "registrar_boton" not in ciclo_src)
check("ciclo no lee la URL del túnel", "url_del_tunel" not in ciclo_src)
check("ciclo sigue reviviendo el bot", "lanzar_bot" in ciclo_src)
check("ciclo sigue vigilando el server local", "127.0.0.1:5577" in ciclo_src)

# ---------- 2) renovar_tunel() hace el flujo completo ----------
rsrc = inspect.getsource(watchdog.renovar_tunel)
check("renovar_tunel mata zombis", "matar_tunel()" in rsrc)
check("renovar_tunel relanza el túnel", "lanzar_tunel()" in rsrc)
check("renovar_tunel espera la URL nueva", "url_del_tunel()" in rsrc)
check("renovar_tunel valida por HTTP", "http_ok" in rsrc)
check("renovar_tunel guarda la URL", "guardar_url(url)" in rsrc)
check("renovar_tunel re-registra el botón sin aviso duplicado", "registrar_boton(url, avisar=False)" in rsrc)
check("renovar_tunel devuelve URL o ''", "return url" in rsrc and 'return ""' in rsrc)
check("registrar_boton tiene parámetro avisar",
      "avisar: bool = True" in inspect.getsource(watchdog.registrar_boton))

# ---------- 3) el bot expone /renovar con auth ----------
comandos = []
for h in bot.bot.message_handlers:
    filtros = h.get("filters", {}) if isinstance(h, dict) else (getattr(h, "filters", {}) or {})
    cmds = filtros.get("commands") if isinstance(filtros, dict) else None
    if cmds:
        comandos.extend(cmds)
check("bot registra /renovar", "renovar" in comandos, f"registrados: {comandos}")
renovar_src = inspect.getsource(bot.cmd_renovar)
check("/renovar exige allowed()", "if not allowed(m.from_user.id)" in renovar_src)
check("/renovar llama a watchdog.renovar_tunel", "watchdog.renovar_tunel()" in renovar_src)
check("/renovar responde con el link nuevo", "Link renovado" in renovar_src)
check("/renovar avisa si falló", "No pude recuperar la URL" in renovar_src)

# ---------- 4) el catch-all no tapea /renovar ----------
if isinstance(bot.bot.message_handlers[0], dict):
    catchall = [h for h in bot.bot.message_handlers
                if h.get("function") is not None and h["function"].__name__ == "on_text"]
    check("on_text ignora comandos",
          bool(catchall)
          and "startswith(\"/\")" in inspect.getsource(catchall[0]["filters"]["func"]))
else:
    catchall = [h for h in bot.bot.message_handlers if getattr(h, "func", None) is not None
                and h.func.__name__ == "on_text"]
    check("on_text ignora comandos",
          bool(catchall) and "startswith(\"/\")" in inspect.getsource(catchall[0].func))

print()
if failures:
    print(f"FAILED: {len(failures)} checks")
    sys.exit(1)
print("OK: todos los checks pasaron")

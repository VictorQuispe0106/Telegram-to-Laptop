"""mini_app_server.py — Backend de la Mini App "Ocupabilidad" para Telegram.

Sirve:
  /            -> frontend (mini_app/index.html + Chart.js)
  /api/registrar   POST {fecha?, dry_run?}  -> registra horas del dia (reusa la
                logica probada de llenar_horas_automatico.py del proyecto
                OCUPABILIDAD AUTOMATIZACION)
  /api/estado   GET ?fecha=  -> si la fecha ya esta registrada
  /api/reportes GET          -> datos JSON para las graficas

Autenticacion: Telegram initData validado con HMAC-SHA256 (firma real),
whitelist de user IDs. La BD es la misma del sistema Ocupabilidad.

Puerto: 5577 (solo accesible via tunel HTTPS).
"""
import datetime
import hashlib
import hmac
import json
import sys
import urllib.parse
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

BASE = Path(__file__).resolve().parent
OCUP_DIR = r"C:\Users\E758455\Desktop\Opencode-proyects\OCUPABILIDAD AUTOMATIZACION"
sys.path.insert(0, BASE.as_posix())
sys.path.insert(0, OCUP_DIR)

from dotenv import find_dotenv, load_dotenv  # noqa: E402
import os  # noqa: E402

load_dotenv(find_dotenv(str(BASE / ".env")))
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Whitelist de la Mini App: usa MINIAPP_CHAT_ID si existe; si no, el primer ID
# de ALLOWED_USER_IDS (mismo parseo que bot.py). Sin ninguno -> vacia (rechaza todo).

def _miniapp_ids() -> set:
    mid = os.getenv("MINIAPP_CHAT_ID", "").strip()
    if not mid:
        for x in os.getenv("ALLOWED_USER_IDS", "").replace(";", ",").split(","):
            if x.strip():
                mid = x.strip()
                break
    return {mid} if mid else set()


ALLOWED_MINIAPP_IDS = _miniapp_ids()  # Victor

import db as llenar  # noqa: E402 (capa DB unificada del proyecto OCUPABILIDAD)
from config import AREA_FILTRO, HORAS_ASIGNADAS, PERFIL  # noqa: E402
import pymssql  # noqa: E402

app = Flask(__name__, static_folder=None)
MINIAPP_DIR = BASE / "mini_app"

# AREA_FILTRO/PERFIL/HORAS_ASIGNADAS vienen de config.py de OCUPABILIDAD


def _conectar_bd():
    return llenar.conectar_bd()


# ---------------- auth ----------------
def _usuario_de_initdata() -> dict:
    """Valida el initData de Telegram y devuelve el user dict. Excepcion si invalido."""
    raw = request.headers.get("X-Init-Data", "")
    if not raw:
        raise PermissionError("sin initData")
    parsed = dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))
    hash_recibido = parsed.pop("hash", "")
    if not hash_recibido:
        raise PermissionError("sin hash")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculado = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculado, hash_recibido):
        raise PermissionError("firma invalida")
    auth_date = int(parsed.get("auth_date", "0"))
    if datetime.datetime.now().timestamp() - auth_date > 24 * 3600:
        raise PermissionError("initData vencido")
    user = json.loads(parsed.get("user", "{}"))
    if str(user.get("id")) not in ALLOWED_MINIAPP_IDS:
        raise PermissionError(f"usuario no autorizado: {user.get('id')}")
    return user


@app.after_request
def _no_cache(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------- frontend ----------------
@app.route("/")
def index():
    return send_from_directory(MINIAPP_DIR, "index.html")


@app.route("/app.js")
def appjs():
    return send_from_directory(MINIAPP_DIR, "app.js")


# ---------------- API ----------------
@app.route("/api/estado")
def api_estado():
    try:
        user = _usuario_de_initdata()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 401
    fecha = request.args.get("fecha") or datetime.date.today().isoformat()
    conn = _conectar_bd()
    try:
        cur = conn.cursor()
        idanalista = llenar.obtener_id(cur, "analista", "idanalista", "nomanalista",
                                       "estanalista", PERFIL["nomanalista"])
        ids = {k: llenar.obtener_id(cur, t, ci, cn, ce, PERFIL[v])
               for k, t, ci, cn, ce, v in [
                   ("idcelda", "celda", "idcelda", "nomcelda", "estcelda", "nomcelda"),
                   ("idoficina", "oficina", "idoficina", "nomoficina", "estoficina", "nomoficina"),
                   ("idturnos", "turnos", "idturnos", "nomturnos", "estturnos", "nomturnos"),
                   ("idareas", "areas", "idareas", "nomareas", "estareas", "nomareas"),
               ]}
        existentes = llenar.obtener_operaciones_existentes(
            cur, idanalista, ids["idcelda"], ids["idoficina"], ids["idturnos"], ids["idareas"], fecha)
        return jsonify({"fecha": fecha, "registrado": bool(existentes),
                        "actividades": len(existentes), "usuario": user.get("first_name", "")})
    finally:
        conn.close()


@app.route("/api/registrar", methods=["POST"])
def api_registrar():
    try:
        user = _usuario_de_initdata()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 401
    # force=True: JSON sin Content-Type application/json tambien se parsea,
    # asi un cliente sin header no activa una escritura real por accidente.
    body = request.get_json(silent=True, force=True) or {}
    fecha = body.get("fecha") or datetime.date.today().isoformat()
    dry_run = bool(body.get("dry_run"))
    fecha_d = datetime.datetime.strptime(fecha, "%Y-%m-%d").date()
    conn = _conectar_bd()
    try:
        cur = conn.cursor()
        plan = llenar.preparar_plan(cur, fecha_d)
        # modo sin tocar BD si dry_run
        modo = "ACTUALIZAR" if plan["existentes"] else "INSERTAR"
        if not dry_run:
            llenar.ejecutar_plan(cur, plan, fecha_d, fecha_d, True)
            conn.commit()
        return jsonify({
            "ok": True,
            "dry_run": dry_run,
            "modo": modo,
            "actividades": len(plan["actividades"]),
            "horas": HORAS_ASIGNADAS,
        })
    except Exception as e:
        if not dry_run:
            conn.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        conn.close()


@app.route("/api/reportes")
def api_reportes():
    """Espejo del api_estadisticas_mes del original: analistas + evolución + totals."""
    try:
        user = _usuario_de_initdata()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 401
    hoy = datetime.date.today()
    try:
        anio = int(request.args.get("anio") or hoy.year)
        mes_n = int(request.args.get("mes") or hoy.month)
    except ValueError:
        anio, mes_n = hoy.year, hoy.month

    conn = _conectar_bd()
    try:
        cur = conn.cursor()
        import calendar
        dias_del_mes = calendar.monthrange(anio, mes_n)[1]

        cur.execute("""
            SELECT a.nomanalista,
                   COUNT(DISTINCT h.fechatrabajo) as dias_registrados,
                   ISNULL(SUM(h.canthoras), 0) as total_horas
            FROM horasasignadas h
            INNER JOIN analista a ON h.idanalista = a.idanalista
            INNER JOIN areas ar ON h.idareas = ar.idareas
            WHERE MONTH(h.fechatrabajo) = %s AND YEAR(h.fechatrabajo) = %s
              AND ar.nomareas = %s
            GROUP BY a.nomanalista
            ORDER BY total_horas DESC
        """, (mes_n, anio, AREA_FILTRO))
        analistas = []
        total_horas = 0.0
        total_dias = 0
        for row in cur.fetchall():
            horas = float(row["total_horas"])
            dias = int(row["dias_registrados"])
            total_horas += horas
            total_dias += dias
            analistas.append({
                "nombre": row["nomanalista"], "dias_registrados": dias, "total_horas": horas,
            })
        for a in analistas:
            a["porcentaje"] = round(a["total_horas"] / total_horas * 100, 1) if total_horas > 0 else 0
            a["promedio_dia"] = round(a["total_horas"] / a["dias_registrados"], 1) if a["dias_registrados"] else 0
            a["cumplimiento"] = round(a["dias_registrados"] / dias_del_mes * 100, 1)

        cur.execute("""
            SELECT a.nomanalista as nombre, DAY(h.fechatrabajo) as dia, SUM(h.canthorasdecimal) as horas
            FROM horasasignadas h
            INNER JOIN analista a ON h.idanalista = a.idanalista
            INNER JOIN areas ar ON h.idareas = ar.idareas
            WHERE MONTH(h.fechatrabajo) = %s AND YEAR(h.fechatrabajo) = %s
              AND ar.nomareas = %s
            GROUP BY a.nomanalista, DAY(h.fechatrabajo), h.fechatrabajo
            ORDER BY a.nomanalista, DAY(h.fechatrabajo)
        """, (mes_n, anio, AREA_FILTRO))
        evolucion: dict[str, dict[int, float]] = {}
        for r in cur.fetchall():
            evolucion.setdefault(r["nombre"], {})[int(r["dia"])] = float(r["horas"] or 0)

        # heatmap propio: dias que llenó el usuario (Victor)
        cur.execute("""
            SELECT DAY(h.fechatrabajo) as dia, SUM(h.canthorasdecimal) as horas
            FROM horasasignadas h
            INNER JOIN analista a ON h.idanalista = a.idanalista
            WHERE a.nomanalista = %s AND YEAR(h.fechatrabajo) = %s AND MONTH(h.fechatrabajo) = %s
            GROUP BY DAY(h.fechatrabajo), h.fechatrabajo
        """, (PERFIL["nomanalista"], anio, mes_n))
        dias_usuario = {int(r["dia"]): float(r["horas"] or 0) for r in cur.fetchall()}

        return jsonify({
            "success": True,
            "anio": anio, "mes": mes_n,
            "dias_del_mes": dias_del_mes,
            "analistas": analistas,
            "total_horas": total_horas,
            "total_dias": total_dias,
            "evolucion": {n: {str(d): h for d, h in m.items()} for n, m in evolucion.items()},
            "dias_usuario": dias_usuario,
        })
    finally:
        conn.close()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5577, debug=False)

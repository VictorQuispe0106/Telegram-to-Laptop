"""disk_clean.py — Limpieza segura del disco C: con preview y confirmacion.

Solo limpia contenido 100% regenerable (temporales y caches). Devuelve
tamanos exactos ANTES de borrar para que el usuario confirme.
"""
import subprocess

# (etiqueta, ruta, requiere_admin)
OBJETIVOS = [
    ("Temp del usuario", r"%TEMP%", False),
    ("Temp de Windows", r"C:\Windows\Temp", True),
    ("Cache Windows Update", r"C:\Windows\SoftwareDistribution\Download", True),
    ("Cache Edge", r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cache", False),
    ("Cache Chrome", r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cache", False),
    ("Miniaturas", r"%LOCALAPPDATA%\Microsoft\Windows\Explorer\thumbcache_*.db", False),
]

PROTEGIDOS_DISCO = ("recycle", "papelera")  # la papelera solo con opt-in explicito


def _expand(ruta: str) -> str:
    return ruta.replace("%TEMP%", subprocess.run(
        ["powershell", "-NoProfile", "-Command", "$env:TEMP"],
        capture_output=True, text=True, timeout=15,
    ).stdout.strip())


def _carpeta_size(ruta: str) -> int:
    """Bytes totales de una carpeta (0 si no existe o sin acceso)."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"$s = (Get-ChildItem -Path '{ruta}' -Recurse -Force -ErrorAction SilentlyContinue | "
         "Measure-Object -Property Length -Sum).Sum; "
         "if ($s) { $s } else { 0 }"],
        capture_output=True, text=True, timeout=120,
    ).stdout.strip()
    try:
        return int(out)
    except ValueError:
        return 0


def _gb(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024:.0f} KB"


def preview() -> str:
    """Lista los objetivos con tamano y total liberable."""
    lines = ["🧹 Limpieza disponible (solo regenerable):"]
    total = 0
    for etiqueta, ruta, admin in OBJETIVOS:
        real = _expand(ruta)
        size = _carpeta_size(real)
        total += size
        mark = " 🔒(necesita admin)" if admin else ""
        lines.append(f"• {etiqueta}: {_gb(size)}{mark}")
    lines.append(f"\n📦 Total liberable: {_gb(total)}")
    lines.append("Confirmá con el botón para ejecutar.")
    return "\n".join(lines)


def limpiar(solo_sin_admin: bool = True) -> str:
    """Borra el contenido de los objetivos. Devuelve bytes liberados y errores."""
    liberado = 0
    errores = []
    for etiqueta, ruta, admin in OBJETIVOS:
        if admin and solo_sin_admin:
            continue
        real = _expand(ruta)
        size = _carpeta_size(real)
        if size == 0:
            continue
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-ChildItem -Path '{real}' -Recurse -Force -ErrorAction SilentlyContinue | "
             "Remove-Item -Recurse -Force -ErrorAction SilentlyContinue"],
            capture_output=True, timeout=300,
        )
        resto = _carpeta_size(real)
        limpieza = size - resto
        liberado += limpieza
        if limpieza < size * 0.5:
            errores.append(etiqueta)
    out = [f"🧹 Liberados {_gb(liberado)} del disco C:."]
    if errores:
        out.append(f"⚠️ Parciales (en uso): {', '.join(errores)}")
    return "\n".join(out)

"""quick_actions.py — Acciones instantaneas de la laptop (sin agente IA).

Screenshot, pantalla off, suspender, volumen, portapapeles.
Todo con ctypes/PowerShell nativo; sin dependencias nuevas.
"""
import ctypes
import subprocess
import time
from pathlib import Path

u32 = ctypes.windll.user32

# ---------- screenshot ----------
def screenshot() -> str:
    """Captura la pantalla y devuelve la ruta del PNG."""
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$b = New-Object Drawing.Bitmap([Windows.Forms.Screen]::PrimaryScreen.Bounds.Width,"
        " [Windows.Forms.Screen]::PrimaryScreen.Bounds.Height);"
        "$g = [Drawing.Graphics]::FromImage($b);"
        "$g.CopyFromScreen(0,0,0,0,$b.Size);"
        "$p = Join-Path $env:TEMP ('spidervic_shot_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.png');"
        "$b.Save($p); Write-Output $p"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    if not out or not Path(out).exists():
        raise RuntimeError("screenshot fallo")
    return out


# ---------- pantalla / energia ----------
def screen_off() -> None:
    """Apaga la pantalla (vuelve al mover mouse/tecla)."""
    HWND_BROADCAST = 0xFFFF
    WM_SC_MONITORPOWER = 0x0172
    SC_MONITORPOWER_MODE_OFF = 2
    u32.SendMessageTimeoutW(
        HWND_BROADCAST, WM_SC_MONITORPOWER, 0xFFFFFFFF,
        SC_MONITORPOWER_MODE_OFF, 0x0002, 1000, None,
    )


def suspend() -> None:
    """Suspende la laptop (sleep)."""
    powrprof = ctypes.windll.powrprof
    # SetSuspendState(Hibernate=FALSE, ForceCritical=FALSE, DisableWakeEvent=FALSE)
    if not powrprof.SetSuspendState(0, 0, 0):
        # algunos equipos necesitan el ejecutable
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Add-Type -AssemblyName System.Windows.Forms;"
             "[Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false)"],
            timeout=15,
        )


def shutdown(mode: str = "apagar") -> None:
    """Apaga o reinicia (llamar solo tras confirmacion humana)."""
    flag = "/s" if mode == "apagar" else "/r"
    subprocess.run(["shutdown", flag, "/t", "10", "/c", "SpiderVic Assistant"], timeout=15)


# ---------- volumen ----------
VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP = 0xAD, 0xAE, 0xAF
KEYEVENTF_KEYUP = 0x0002


def _key(vk: int, veces: int = 1):
    for _ in range(veces):
        u32.keybd_event(vk, 0, 0, 0)
        u32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.05)


def volume_up(n: int = 5) -> None:
    """Sube volumen n pasos de 2% (50 = 100%)."""
    _key(VK_VOLUME_UP, n)


def volume_down(n: int = 5) -> None:
    _key(VK_VOLUME_DOWN, n)


def volume_mute() -> None:
    _key(VK_VOLUME_MUTE, 1)


# ---------- portapapeles ----------
CF_UNICODETEXT = 13


def clipboard_text() -> str:
    """Lee el texto del portapapeles de la sesion interactiva."""
    if not u32.OpenClipboard(0):
        raise RuntimeError("portapapeles ocupado")
    try:
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        ptr = ctypes.windll.kernel32.GlobalLock(h)
        if not ptr:
            return ""
        try:
            text = ctypes.c_wchar_p(ptr).value or ""
            return text
        finally:
            ctypes.windll.kernel32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()

"""forti_vpn.py — Control SSL VPN de FortiClient via automatizacion del tray.

Ruta probada en vivo (FortiClient 7.4.3, Win11, E758455):
1. Chevron "Mostrar iconos ocultos" (Shell_TrayWnd) -> flyout
   TopLevelWindowForOverflowXamlIsland
2. Boton FortiClient en el flyout (tooltip: 'FortiClient - Connected to...')
3. Click derecho (SendInput) -> menu #32768 con
   'Connect "TUNNEL"' / 'Disconnect "TUNNEL"'
4. Verificacion de estado: adaptador 'Fortinet SSL VPN Virtual Ethernet
   Adapter' Up/Down (Get-NetAdapter).

La API COM FortiClient.VPN solo soporta IPsec (SSL no), y el CLI de
FortiTray/FortiVPN 7.4.3 no conecta; el tray menu es la via oficial-de-facto.
"""
import subprocess
import time

TUNNEL = "Ciberseguridad_Web"
VPN_ADAPTER = "Ethernet 2"  # Fortinet SSL VPN Virtual Ethernet Adapter


def vpn_up() -> bool:
    """True si el adaptador SSL VPN esta Up."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-NetAdapter -Name '{VPN_ADAPTER}' -ErrorAction SilentlyContinue).Status"],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    return out == "Up"


def _esc():
    import ctypes
    u32 = ctypes.windll.user32
    u32.keybd_event(0x1B, 0, 0, 0)
    u32.keybd_event(0x1B, 0, 2, 0)


def _mouse_right(x, y):
    import ctypes
    u32 = ctypes.windll.user32
    u32.SetCursorPos(int(x), int(y))
    time.sleep(0.3)
    u32.mouse_event(0x0008, 0, 0, 0, 0)
    time.sleep(0.05)
    u32.mouse_event(0x0010, 0, 0, 0, 0)


def _tray_menu_item(accion: str, tunnel: str = TUNNEL) -> bool:
    """Abre flyout -> right-click FortiTray -> clickea 'accion "tunnel"'.

    accion: 'Connect' o 'Disconnect'. Devuelve True si el item fue clickeado.
    """
    from pywinauto import Desktop

    d = Desktop(backend="uia")

    def chevron():
        tray = d.window(class_name="Shell_TrayWnd")
        for c in tray.descendants(control_type="Button"):
            if (c.window_text() or "").strip() == "Mostrar iconos ocultos":
                return c
        return None

    def flyout():
        for w in d.windows(top_level_only=True):
            if (w.class_name() or "").startswith("TopLevelWindowForOverflow") and w.is_visible():
                return w
        return None

    def forti(fly):
        # SOLO iconos de notificacion (SystemTrayIcon), nunca botones de taskbar
        # (right-click en taskbar button abre jump list, no el menu de Forti)
        for c in fly.descendants(control_type="Button"):
            if "Forti" in (c.window_text() or ""):
                return c
        tray = d.window(class_name="Shell_TrayWnd")
        for c in tray.descendants(control_type="Button"):
            aid = c.element_info.automation_id or ""
            if aid == "SystemTrayIcon" and "Forti" in (c.window_text() or ""):
                return c
        return None

    # 1. abrir flyout (reintentos: el flyout es caprichoso)
    fly = None
    for intento in range(4):
        fly = flyout()
        if fly:
            break
        ch = chevron()
        if not ch:
            raise RuntimeError("chevron del tray no encontrado")
        ch.click_input()
        time.sleep(3.5)
    if not fly:
        raise RuntimeError("flyout de iconos ocultos no abrio")

    # 2. icono de FortiClient (reintento por render lento)
    icono = None
    for _ in range(6):
        icono = forti(fly)
        if icono:
            break
        time.sleep(1.5)
    if not icono:
        raise RuntimeError("icono FortiTray no visible en el flyout")

    # 3. right-click -> menu #32768
    r = icono.rectangle()
    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    _mouse_right(cx, cy)
    time.sleep(2.5)

    menu = None
    for _ in range(10):
        for w in d.windows(top_level_only=True, class_name="#32768"):
            menu = w
            break
        if menu:
            break
        time.sleep(0.8)
    if not menu:
        _esc()
        time.sleep(0.5)
        raise RuntimeError("menu contextual de FortiTray no abrio")

    # 4. item objetivo: 'Connect to "TUNNEL"' (desconectado) o 'Disconnect "TUNNEL"'
    #    (Forti omite el 'to' en Disconnect; match por verb + nombre)
    item = None
    for c in menu.descendants(control_type="MenuItem"):
        t = (c.window_text() or "").strip()
        if t.startswith(accion) and f'"{tunnel}"' in t:
            item = c
            break
    if not item:
        disponibles = [c.window_text() for c in menu.descendants(control_type="MenuItem")]
        raise RuntimeError(f"item '{accion} \"{tunnel}\"' no en menu; hay: {disponibles}")

    r2 = item.rectangle()
    import ctypes
    u32 = ctypes.windll.user32
    u32.SetCursorPos((r2.left + r2.right) // 2, (r2.top + r2.bottom) // 2)
    time.sleep(0.2)
    u32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    u32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
    return True


def connect(tunnel: str = TUNNEL, wait_s: int = 45) -> bool:
    """Conecta el tunnel y verifica el adaptador. True si queda Up."""
    if vpn_up():
        return True
    _tray_menu_item("Connect", tunnel)
    fin = time.time() + wait_s
    while time.time() < fin:
        time.sleep(3)
        if vpn_up():
            return True
    return False


def disconnect(tunnel: str = TUNNEL, wait_s: int = 30) -> bool:
    """Desconecta el tunnel y verifica. True si queda Down."""
    if not vpn_up():
        return True
    _tray_menu_item("Disconnect", tunnel)
    fin = time.time() + wait_s
    while time.time() < fin:
        time.sleep(3)
        if not vpn_up():
            return True
    return False

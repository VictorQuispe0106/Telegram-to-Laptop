"""net_scan.py — Escaneo de dispositivos en la red local activa.

1. Detecta la subnet del adaptador con ruta por defecto (gateway).
2. Ping sweep rapido (/24) para llenar la tabla ARP.
3. Parsea `arp -a` y resuelve hostname (best-effort).
4. Compara con net_known.json (baseline): marca 🆕 dispositivos nuevos.
"""
import concurrent.futures
import json
import os
import re
import socket
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

KNOWN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "net_known.json")


def _ps(cmd: str) -> str:
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()


def adaptador_activo():
    """(ip, prefijo) del adaptador con gateway por defecto."""
    gw = _ps("(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue "
             "| Sort-Object RouteMetric | Select-Object -First 1).InterfaceIndex")
    if not gw:
        return None
    out = _ps(f"(Get-NetIPAddress -InterfaceIndex {gw} -AddressFamily IPv4 -ErrorAction "
              "SilentlyContinue | Select-Object -First 1).IPAddress")
    if not out:
        return None
    return out


def subnet_de(ip: str) -> str:
    partes = ip.split(".")
    return ".".join(partes[:3])


def udp_sweep(sub: str):
    """Llena la tabla ARP enviando 1 datagrama UDP por IP (sin ICMP).
    Truco clásico: ARP se resuelve al enviar cualquier paquete. Rapidísimo."""
    import socket as s
    sock = s.socket(s.AF_INET, s.SOCK_DGRAM)
    sock.settimeout(0)
    for i in range(1, 255):
        try:
            sock.sendto(b"\x00", (f"{sub}.{i}", 9))
        except Exception:
            pass
    sock.close()
    import time
    time.sleep(2.5)  # margen para que el ARP resuelva


def arp_del_subred(sub: str):
    """[(ip, mac)] del arp -a filtrando la subnet."""
    out = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=30).stdout
    pares = []
    for linea in out.splitlines():
        m = re.match(rf"\s*({re.escape(sub)}\.\d+)\s+([0-9a-fA-F-]{{17}})\s", linea)
        if m:
            mac = m.group(2).replace("-", ":").lower()
            if mac != "ff:ff:ff:ff:ff:ff":
                pares.append((m.group(1), mac))
    return pares


def hostname(ip: str) -> str:
    try:
        socket.setdefaulttimeout(1.5)
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""


def cargar_conocidos() -> dict:
    try:
        with open(KNOWN_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def guardar_conocidos(k: dict):
    with open(KNOWN_FILE, "w", encoding="utf-8") as f:
        json.dump(k, f, indent=2, ensure_ascii=False)


def escanear() -> dict:
    """Ejecuta el escaneo y devuelve el reporte."""
    import time
    ip_local = adaptador_activo()
    if not ip_local:
        return {"error": "sin adaptador con gateway activo"}
    sub = subnet_de(ip_local)
    t0 = time.time()
    udp_sweep(sub)
    pares = arp_del_subred(sub)
    # hostnames en paralelo (gethostbyaddr puede colgar)
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        futuros = {ex.submit(hostname, ip): (ip, mac) for ip, mac in pares}
        conocidos = cargar_conocidos()
        dispositivo = []
        for fut in concurrent.futures.as_completed(futuros, timeout=20):
            ip, mac = futuros[fut]
            try:
                host = fut.result()
            except Exception:
                host = ""
            nuevo = mac not in conocidos
            dispositivo.append({"ip": ip, "mac": mac, "nuevo": nuevo, "hostname": host})
            if nuevo:
                conocidos[mac] = {"ip": ip, "hostname": host,
                                  "primera_vez": time.strftime("%Y-%m-%d %H:%M")}
    guardar_conocidos(conocidos)
    dispositivo.sort(key=lambda d: tuple(int(x) for x in d["ip"].split(".")))
    return {"subred": sub + ".0/24", "ip_local": ip_local, "segundos": round(time.time() - t0, 1),
            "total": len(dispositivo), "dispositivos": dispositivo}


if __name__ == "__main__":
    r = escanear()
    print(json.dumps(r, indent=2, ensure_ascii=False))

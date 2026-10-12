"""Reinicio diferido y quirúrgico del bot, desacoplado del agente RPC en curso.

Contexto: restart_bot.py mata bot.py con /T, lo que arrastra el árbol hijo
(incluye el shell RPC que sirve la sesión del agente) y mata al agente a
mitad de su turno. Este script hace el mismo trabajo sin ese problema:

1. Espera un margen (por defecto 75s) para que el turno del agente termine
   y su mensaje llegue por Telegram.
2. Mata SOLO el proceso del bot viejo (sin /T: los hijos quedan huérfanos
   y siguen vivos hasta que el paso 3 los limpie).
3. Mata el shell RPC huérfano del bot viejo (ya sin servir a nadie).

Uso:
    python scripts/restart_bot_delayed.py <pid_bot_viejo> <pid_rpc_viejo> [margen_s]
"""
import sys
import time

import psutil


def main():
    old_bot = int(sys.argv[1])
    old_rpc = int(sys.argv[2])
    margen = int(sys.argv[3]) if len(sys.argv) > 3 else 75
    print(f"kill diferido en {margen}s: bot {old_bot}, rpc {old_rpc}", flush=True)
    time.sleep(margen)
    for pid, nombre in ((old_bot, "bot.py"), (old_rpc, "gentle-shell rpc")):
        try:
            psutil.Process(pid).kill()
            print(f"muerto {nombre} (pid {pid})", flush=True)
        except psutil.NoSuchProcess:
            print(f"{nombre} (pid {pid}) ya estaba muerto", flush=True)
        except Exception as e:
            print(f"error matando {pid}: {e}", flush=True)


if __name__ == "__main__":
    main()

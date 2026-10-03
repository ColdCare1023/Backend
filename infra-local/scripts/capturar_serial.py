"""Guarda el monitor serie del nodo con marca de hora local. Uso: capturar_serial.py <archivo> <segundos> [COM6]"""
import sys
import time

import serial

archivo, segundos = sys.argv[1], float(sys.argv[2])
puerto = sys.argv[3] if len(sys.argv) > 3 else "COM6"
# Abrir con DTR/RTS bajos: con los valores por defecto, abrir el puerto reinicia el ESP32.
s = serial.Serial(None, 115200, timeout=1)
s.port, s.dtr, s.rts = puerto, False, False
s.open()
fin = time.time() + segundos
with open(archivo, "a", encoding="utf-8") as f:
    while time.time() < fin:
        crudo = s.readline()
        if crudo:
            t = crudo.decode("utf-8", errors="replace").rstrip()
            if t.strip():
                f.write(time.strftime("%H:%M:%S ") + t + "\n")
                f.flush()

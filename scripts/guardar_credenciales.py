"""Guarda credenciales en un archivo .env local SIN mostrarlas ni pasarlas por argumento.

Pide cada valor con `getpass` (no se ve al pegarlo, no queda en el historial del shell ni en el
chat) y lo escribe en la línea `CLAVE=` del archivo. Un valor vacío deja la línea como estaba.

Uso:
    python -m scripts.guardar_credenciales                      # los de Twilio, en notificaciones.local.env
    python -m scripts.guardar_credenciales SMTP_PASSWORD        # claves a elegir
    python -m scripts.guardar_credenciales --archivo otro.env SMS_FROM
"""

from __future__ import annotations

import argparse
import getpass
import re
import sys
from pathlib import Path

CLAVES_TWILIO = ("SMS_ACCOUNT_SID", "SMS_AUTH_TOKEN", "SMS_FROM")

# Formato esperado por clave: un error de pegado se detecta antes de guardar.
FORMATOS = {
    "SMS_ACCOUNT_SID": (re.compile(r"^AC[0-9a-fA-F]{32}$"), "empieza con AC y tiene 34 caracteres"),
    "SMS_AUTH_TOKEN": (re.compile(r"^[0-9a-fA-F]{32}$"), "son 32 caracteres hexadecimales"),
    "SMS_FROM": (re.compile(r"^\+[1-9]\d{7,14}$"), "formato internacional, p. ej. +15551234567"),
}


# Al pegar, muchas terminales envuelven el texto en marcadores (ESC[200~ ... ESC[201~) que getpass
# deja pasar: contaban como parte del valor y un valor correcto parecía inválido.
_MARCADORES_PEGADO = re.compile(r"(?:\x1b)?\[20[01]~")
_SECUENCIAS_ESC = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def limpiar(valor: str) -> str:
    """Quita marcadores de pegado, secuencias de escape, espacios y caracteres no imprimibles."""
    valor = _SECUENCIAS_ESC.sub("", _MARCADORES_PEGADO.sub("", valor))
    return "".join(c for c in valor if c.isprintable() and not c.isspace())


def validar(clave: str, valor: str) -> str | None:
    """None si el valor sirve; si no, qué se esperaba."""
    formato = FORMATOS.get(clave)
    if formato is None or formato[0].match(valor):
        return None
    return formato[1]


def establecer(texto: str, clave: str, valor: str) -> str:
    """Pone `clave=valor` en el texto del .env (la reemplaza o la añade). Sin regex sobre el valor,
    así que `|`, `&`, `\\` y `$` en el valor no rompen nada."""
    lineas = texto.splitlines()
    nueva = f"{clave}={valor}"
    for i, linea in enumerate(lineas):
        if linea.startswith(f"{clave}="):
            lineas[i] = nueva
            break
    else:
        lineas.append(nueva)
    return "\n".join(lineas) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("claves", nargs="*", default=list(CLAVES_TWILIO))
    parser.add_argument("--archivo", default="notificaciones.local.env")
    args = parser.parse_args(argv)
    ruta = Path(args.archivo)
    if not ruta.exists():
        sys.exit(f"No existe {ruta}: corre el comando desde la carpeta Backend.")
    texto = ruta.read_text(encoding="utf-8")
    guardadas = []
    for clave in args.claves:
        valor = limpiar(getpass.getpass(f"{clave} (no se ve; Enter vacío = no cambiar): "))
        if not valor:
            continue
        error = validar(clave, valor)
        if error:
            # Solo el largo y el inicio permitido: nunca el valor.
            print(f"  ✗ {clave} no parece correcto ({error}); recibí {len(valor)} caracteres. No se guardó.")
            continue
        texto = establecer(texto, clave, valor)
        guardadas.append(clave)
    ruta.write_text(texto, encoding="utf-8")
    print("Guardadas:", ", ".join(guardadas) if guardadas else "ninguna")
    return 0 if guardadas or not args.claves else 1


if __name__ == "__main__":
    raise SystemExit(main())

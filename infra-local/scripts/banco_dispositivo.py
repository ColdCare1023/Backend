"""ADR-18: dispositivo de banco para el ensayo de saturación del buffer.

El ensayo envía lecturas sintéticas ("simulated": true) y el backend solo las
acepta de un dispositivo dado de alta como demo (BE-015). El frontend no expone
`es_demo` a propósito (un dispositivo demo en el local admitiría datos
simulados) y en producción la API lo rechaza: este script lo da de alta en la
pila local.

Uso:
  python scripts/banco_dispositivo.py alta  [--device-id ID] [--aceptar-privacidad]
  python scripts/banco_dispositivo.py token [--device-id ID]
  python scripts/banco_dispositivo.py baja  [--device-id ID] [--descripcion TEXTO]

  alta   da de alta el dispositivo con es_demo=true («Banco ADR-18») y guarda su
         token en secrets/<device_id>.token; nunca lo imprime.
  token  rotación preventiva: reemplaza el token guardado. Con
         BROKER_USUARIOS_MODO=api el backend crea el usuario en el EMQX local.
  baja   al terminar el ensayo (motivo fin_de_servicio), para que no abra
         incidentes «Sin datos». Un device_id no se reutiliza: el siguiente
         ensayo usa otro (ESP32_BANCO_02, ...).

`token` y `baja` se niegan si el dispositivo no es demo: no pueden tocar el
equipo del local por un error de tipeo. El orden del ensayo (vaciar, cambiar
identidad, llenar, vaciar, restaurar) está en IoT docs/decisiones_tecnicas.md,
ADR-18.
"""
import argparse
import os

import httpx
from aprovisionar import API, RAIZ, cabecera_admin

POR_DEFECTO = "ESP32_BANCO_01"


def _guardar_token(device_id: str, token: str) -> None:
    destino = RAIZ / "secrets" / f"{device_id}.token"
    destino.parent.mkdir(exist_ok=True)
    with os.fdopen(os.open(destino, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as archivo:
        archivo.write(token)


def _exigir_demo(http: httpx.Client, cabecera: dict[str, str], device_id: str) -> None:
    r = http.get(f"{API}/api/dispositivos", headers=cabecera)
    r.raise_for_status()
    dispositivo = next((d for d in r.json() if d["id"] == device_id), None)
    if dispositivo is None:
        raise SystemExit(f"{device_id} no existe: créelo con 'alta'.")
    if not dispositivo["es_demo"]:
        raise SystemExit(f"{device_id} no es un dispositivo demo: este script no lo toca.")


def alta(http: httpx.Client, cabecera: dict[str, str], device_id: str, aceptar_privacidad: bool) -> None:
    cuerpo = {"device_id": device_id, "nombre": "Banco ADR-18", "es_demo": True}
    r = http.post(f"{API}/api/dispositivos", headers=cabecera, json=cuerpo)
    if r.status_code == 403 and "privacidad" in r.text:
        # BE-014: igual que crear_dispositivo.py, el consentimiento no se presume.
        if not aceptar_privacidad:
            raise SystemExit(
                "El administrador no ha aceptado la política de privacidad (BE-014). Inicie sesión una vez "
                "en el frontend, o vuelva a ejecutar con --aceptar-privacidad si usted es esa persona y la acepta."
            )
        http.post(f"{API}/api/auth/privacidad/aceptar", headers=cabecera).raise_for_status()
        r = http.post(f"{API}/api/dispositivos", headers=cabecera, json=cuerpo)
    if r.status_code == 409:
        raise SystemExit(f"{device_id} ya existe o se dio de baja; un device_id no se reutiliza: use otro.")
    r.raise_for_status()
    credencial = r.json()["credencial"]
    _guardar_token(device_id, credencial["token"])
    print(f"{device_id}: alta como demo; token en secrets/{device_id}.token")
    if credencial.get("mensaje_broker"):
        print(credencial["mensaje_broker"])


def token(http: httpx.Client, cabecera: dict[str, str], device_id: str) -> None:
    _exigir_demo(http, cabecera, device_id)
    r = http.post(
        f"{API}/api/dispositivos/{device_id}/credenciales/rotar",
        headers=cabecera,
        json={"motivo": "ensayo ADR-18", "causa": "preventiva"},
    )
    r.raise_for_status()
    _guardar_token(device_id, r.json()["token"])
    print(f"{device_id}: credencial rotada; token en secrets/{device_id}.token")
    if r.json().get("mensaje_broker"):
        print(r.json()["mensaje_broker"])


def baja(http: httpx.Client, cabecera: dict[str, str], device_id: str, descripcion: str) -> None:
    _exigir_demo(http, cabecera, device_id)
    r = http.post(
        f"{API}/api/dispositivos/{device_id}/baja",
        headers=cabecera,
        json={"motivo": "fin_de_servicio", "descripcion": descripcion},
    )
    r.raise_for_status()
    print(f"{device_id}: dado de baja ({descripcion}).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("accion", choices=["alta", "token", "baja"])
    parser.add_argument("--device-id", default=POR_DEFECTO)
    parser.add_argument("--aceptar-privacidad", action="store_true")
    parser.add_argument("--descripcion", default="fin del ensayo ADR-18")
    args = parser.parse_args()
    with httpx.Client(timeout=15) as http:
        cabecera = cabecera_admin(http)
        if args.accion == "alta":
            alta(http, cabecera, args.device_id, args.aceptar_privacidad)
        elif args.accion == "token":
            token(http, cabecera, args.device_id)
        else:
            baja(http, cabecera, args.device_id, args.descripcion)


if __name__ == "__main__":
    main()

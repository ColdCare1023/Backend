"""HU-44: tope de filas de auditoría durante una retención por compromiso.

Quien tiene el token filtrado puede publicar en bucle en los tópicos del nodo
(lecturas, eventos, respuestas al desafío). Cada intento se rechaza, pero si
cada uno dejara una fila encadenada, la bitácora crecería sin límite. Se audita
como mucho una fila por clave (dispositivo, tipo y motivo) en cada ventana, con
el número de intentos suprimidos desde la anterior; el resto solo se cuenta.

Vive en memoria: el backend corre con una réplica y un worker (README,
«Topología»). Tras un reinicio, la primera fila de cada clave vuelve a salir.
"""

import time


class LimitadorAuditoria:
    def __init__(self, ventana_segundos: float = 60.0) -> None:
        self._ventana = ventana_segundos
        self._estado: dict[tuple, list] = {}

    def permitir(self, *clave: object) -> int | None:
        """Devuelve cuántos intentos se suprimieron desde la última fila si toca
        auditar, o None si este intento solo se cuenta."""
        ahora = time.monotonic()
        previo = self._estado.get(clave)
        if previo is None or ahora - previo[0] >= self._ventana:
            self._estado[clave] = [ahora, 0]
            return previo[1] if previo else 0
        previo[1] += 1
        return None

    def vaciar(self, device_id: str) -> dict[str, int]:
        """Al terminar la retención de `device_id`: intentos contados y aún no
        auditados por cada clave (p. ej. `pop_fallida:firma_invalida`), y olvida
        sus claves. Las claves son (tipo, device_id, *motivo)."""
        pendientes = {}
        for clave in [c for c in self._estado if len(c) > 1 and c[1] == device_id]:
            contados = self._estado.pop(clave)[1]
            if contados:
                pendientes[":".join(str(p) for p in (clave[0], *clave[2:]))] = contados
        return pendientes

    def reiniciar(self) -> None:
        self._estado.clear()

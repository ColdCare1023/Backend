"""HU-44: tope de filas de auditoría durante una retención por compromiso.

Quien tiene el token filtrado puede publicar en bucle en los tópicos del nodo
(lecturas, eventos, respuestas al desafío). Cada intento se rechaza, pero si
cada uno dejara una fila encadenada, la bitácora crecería sin límite. Se audita
como mucho una fila por clave (dispositivo, tipo y motivo) en cada ventana, con
el número de intentos suprimidos desde la anterior; el resto solo se cuenta.

Lo contado y aún no auditado se vuelca en una fila RETENCION_INTENTOS_SUPRIMIDOS
cuando la retención termina (prueba verificada, revocación, rotación nueva o
baja) y, si quien publicaba deja de hacerlo, cuando su ventana vence (lo
revisa `VigilanteSinDatos` en cada pasada).

Vive en memoria: el backend corre con una réplica y un worker (README,
«Topología»). Tras un reinicio, la primera fila de cada clave vuelve a salir.
"""

import time
from collections.abc import Callable


def _nombre(clave: tuple) -> str:
    """`tipo[:motivo]` de una clave (tipo, device_id, *motivo)."""
    return ":".join(str(p) for p in (clave[0], *clave[2:]))


class LimitadorAuditoria:
    def __init__(self, ventana_segundos: float = 60.0, reloj: Callable[[], float] = time.monotonic) -> None:
        self._ventana = ventana_segundos
        self._reloj = reloj
        self._estado: dict[tuple, list] = {}

    def permitir(self, *clave: object) -> int | None:
        """Devuelve cuántos intentos se suprimieron desde la última fila si toca
        auditar, o None si este intento solo se cuenta."""
        ahora = self._reloj()
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
                pendientes[_nombre(clave)] = contados
        return pendientes

    def vencidos(self) -> dict[str, dict[str, int]]:
        """Intentos contados en claves cuya ventana venció sin otro intento que
        los auditara, por dispositivo y con los nombres de `vaciar`. Reinicia
        esas claves: lo que llegue después se cuenta en una ventana nueva."""
        ahora = self._reloj()
        salida: dict[str, dict[str, int]] = {}
        for clave, estado in self._estado.items():
            if len(clave) > 1 and estado[1] and ahora - estado[0] >= self._ventana:
                salida.setdefault(str(clave[1]), {})[_nombre(clave)] = estado[1]
                estado[0], estado[1] = ahora, 0
        return salida

    def reiniciar(self) -> None:
        self._estado.clear()


# Una sola instancia por proceso: la usan la ingesta MQTT, el router de
# dispositivos (revocar, rotar, baja) y el vigilante periódico.
LIMITE_AUDITORIA = LimitadorAuditoria()


async def auditar_suprimidos(auditoria, device_id: str, suprimidos: dict[str, int], cierre: str) -> None:
    """Fila RETENCION_INTENTOS_SUPRIMIDOS con lo que el limitador solo contó.

    `auditoria` es un AuditarAccionCriticaUseCase. `cierre` dice por qué se
    vuelca: `verificada`, `revocacion`, `rotacion`, `baja` o `ventana_vencida`.
    """
    if not suprimidos:
        return
    await auditoria.execute(
        usuario_id=None,
        accion="RETENCION_INTENTOS_SUPRIMIDOS",
        recurso=f"dispositivos/{device_id}/credenciales",
        detalle={"suprimidos_sin_auditar": suprimidos, "cierre": cierre},
        ip_origen=None,
        device_id=device_id,
    )

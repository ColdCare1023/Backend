"""HU-48: estado del buffer LittleFS del nodo y brechas por capacidad agotada.

El nodo informa cada 5 min (`estado_nodo`) cuántos registros retiene sin
acuse, la capacidad y las muestras que no pudo almacenar; y emite
`buffer_saturado` al abrir y cerrar un episodio de capacidad agotada (y cada
10 min mientras siga abierto). Aquí se persiste lo último de cada uno para
que el tablero lo muestre sin depender de haber estado abierto cuando llegó
el evento SSE.

El `detail` viene del nodo: se toman solo los campos conocidos, con su tipo,
y un valor fuera de tipo se ignora en lugar de rechazar el latido.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from src.domain.repositories.i_estado_nodo_repository import (
    EpisodioBufferSaturado,
    EstadoNodo,
    IEstadoNodoRepository,
)

# B-16: 300 archivos x 100 lecturas. Lo usa la vista cuando el nodo aún no
# informó su `capacidad` (firmware anterior).
CAPACIDAD_BUFFER_POR_DEFECTO = 30_000
CAUSA_NODO_POR_DEFECTO = "buffer_lleno"
# Nombre público de la causa (el que ya usa el tablero) y su texto.
CAUSA_BUFFER_AGOTADO = "buffer_agotado"
DESCRIPCION_BUFFER_AGOTADO = (
    "El almacenamiento local (LittleFS) del nodo se llenó durante una desconexión prolongada "
    "y las muestras de este tramo no se pudieron guardar."
)
BRECHAS_EN_ESTADO_OPERATIVO = 10
_MAX_CAUSA = 40
_UINT32 = 4_294_967_295


def _entero(detalle: dict, clave: str, maximo: int = _UINT32) -> int | None:
    valor = detalle.get(clave)
    if isinstance(valor, bool) or not isinstance(valor, int) or not 0 <= valor <= maximo:
        return None
    return valor


def _instante(detalle: dict, clave: str) -> datetime | None:
    valor = detalle.get(clave)
    if not isinstance(valor, str) or not valor:
        return None
    try:
        instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return instante if instante.tzinfo else instante.replace(tzinfo=timezone.utc)


def _utc(instante: datetime | None) -> datetime | None:
    if instante is None:
        return None
    return instante if instante.tzinfo else instante.replace(tzinfo=timezone.utc)


@dataclass(slots=True, frozen=True)
class LatidoBuffer:
    pendientes: int | None
    capacidad: int | None
    ocupacion_pct: int | None
    perdidas_total: int | None
    cuarentena: int | None
    pendientes_desde: datetime | None
    pendientes_hasta: datetime | None


def interpretar_estado_nodo(detalle: dict | None) -> LatidoBuffer:
    """Campos de buffer de un `estado_nodo` (firmware `publicarEstadoNodo`).
    `pendientes_desde`/`pendientes_hasta` son opcionales: el firmware puede
    informar la hora del pendiente más antiguo y del más nuevo."""
    d = detalle or {}
    return LatidoBuffer(
        pendientes=_entero(d, "pendientes"),
        capacidad=_entero(d, "capacidad") or None,
        ocupacion_pct=_entero(d, "ocupacion_pct", maximo=100),
        perdidas_total=_entero(d, "perdidas_total"),
        cuarentena=_entero(d, "cuarentena"),
        pendientes_desde=_instante(d, "pendientes_desde"),
        pendientes_hasta=_instante(d, "pendientes_hasta"),
    )


@dataclass(slots=True, frozen=True)
class AvisoBufferSaturado:
    perdidas: int
    perdidas_total: int | None
    abierto: bool
    desde: datetime | None
    hasta: datetime | None
    desde_boot_id: int | None
    desde_seq_no: int | None
    hasta_boot_id: int | None
    hasta_seq_no: int | None
    causa: str


def interpretar_buffer_saturado(detalle: dict | None) -> AvisoBufferSaturado:
    """`detail` de `buffer_saturado` (contrato v1.0, ver
    contrato/ejemplos/evento_buffer_saturado.json)."""
    d = detalle or {}
    causa = d.get("causa")
    abierto = d.get("episodio_abierto")
    return AvisoBufferSaturado(
        perdidas=_entero(d, "perdidas_episodio") or 0,
        perdidas_total=_entero(d, "perdidas_total"),
        abierto=abierto if isinstance(abierto, bool) else True,
        desde=_instante(d, "desde"),
        hasta=_instante(d, "hasta"),
        desde_boot_id=_entero(d, "desde_boot_id"),
        desde_seq_no=_entero(d, "desde_seq_no"),
        hasta_boot_id=_entero(d, "hasta_boot_id"),
        hasta_seq_no=_entero(d, "hasta_seq_no"),
        causa=causa[:_MAX_CAUSA] if isinstance(causa, str) and causa else CAUSA_NODO_POR_DEFECTO,
    )


class RegistrarEstadoNodoUseCase:
    def __init__(self, repositorio: IEstadoNodoRepository) -> None:
        self._repo = repositorio

    async def execute(
        self,
        device_id: str,
        detalle: dict | None,
        *,
        recibido_en: datetime,
        ocurrido_en: datetime | None = None,
        boot_id: int | None = None,
    ) -> EstadoNodo:
        latido = interpretar_estado_nodo(detalle)
        previo = await self._repo.obtener_estado(device_id)
        sincronizado = latido.pendientes == 0
        return await self._repo.guardar_estado(EstadoNodo(
            device_id=device_id,
            recibido_en=recibido_en,
            ocurrido_en=ocurrido_en,
            boot_id=boot_id,
            pendientes=latido.pendientes,
            capacidad=latido.capacidad,
            ocupacion_pct=latido.ocupacion_pct,
            perdidas_total=latido.perdidas_total,
            cuarentena=latido.cuarentena,
            pendientes_desde=None if sincronizado else latido.pendientes_desde,
            pendientes_hasta=None if sincronizado else latido.pendientes_hasta,
            # HU-48 esc. 3: con 0 pendientes el nodo sincronizó todo.
            ultima_sincronizacion_en=(
                recibido_en if sincronizado else (previo.ultima_sincronizacion_en if previo else None)
            ),
            detalle=dict(detalle) if detalle else None,
        ))


class RegistrarEpisodioBufferUseCase:
    """Un episodio por primera muestra perdida (`desde_boot_id`, `desde_seq_no`).
    Los avisos siguientes del mismo episodio actualizan su fin, sus pérdidas y
    si sigue abierto; no se duplica la brecha en el tablero."""

    def __init__(self, repositorio: IEstadoNodoRepository) -> None:
        self._repo = repositorio

    async def execute(self, device_id: str, detalle: dict | None, *, recibido_en: datetime) -> EpisodioBufferSaturado:
        aviso = interpretar_buffer_saturado(detalle)
        existente = None
        if aviso.desde_boot_id is not None and aviso.desde_seq_no is not None:
            existente = await self._repo.episodio_por_inicio(device_id, aviso.desde_boot_id, aviso.desde_seq_no)
        abiertos = await self._repo.episodios_abiertos(device_id)
        if existente is None and aviso.desde_seq_no is None and abiertos:
            # Aviso sin identidad del episodio: se asume que continúa el abierto.
            existente = abiertos[0]

        if existente is not None:
            existente.hasta = aviso.hasta or existente.hasta
            existente.hasta_boot_id = aviso.hasta_boot_id if aviso.hasta_boot_id is not None else existente.hasta_boot_id
            existente.hasta_seq_no = aviso.hasta_seq_no if aviso.hasta_seq_no is not None else existente.hasta_seq_no
            existente.desde = existente.desde or aviso.desde
            existente.perdidas = max(existente.perdidas, aviso.perdidas)
            existente.perdidas_total = aviso.perdidas_total if aviso.perdidas_total is not None else existente.perdidas_total
            if existente.abierto and not aviso.abierto:
                existente.cerrado_en = recibido_en
            existente.abierto = aviso.abierto
            existente.actualizado_en = recibido_en
            return await self._repo.actualizar_episodio(existente)

        # El nodo lleva un solo episodio a la vez: si empieza otro, el anterior
        # terminó aunque su cierre no haya llegado (p. ej. reinicio del nodo).
        for anterior in abiertos:
            anterior.abierto = False
            anterior.cerrado_en = recibido_en
            anterior.actualizado_en = recibido_en
            await self._repo.actualizar_episodio(anterior)
        return await self._repo.agregar_episodio(EpisodioBufferSaturado(
            id=uuid4(),
            device_id=device_id,
            causa=aviso.causa,
            perdidas=aviso.perdidas,
            abierto=aviso.abierto,
            registrado_en=recibido_en,
            actualizado_en=recibido_en,
            desde=aviso.desde,
            hasta=aviso.hasta,
            desde_boot_id=aviso.desde_boot_id,
            desde_seq_no=aviso.desde_seq_no,
            hasta_boot_id=aviso.hasta_boot_id,
            hasta_seq_no=aviso.hasta_seq_no,
            perdidas_total=aviso.perdidas_total,
            cerrado_en=None if aviso.abierto else recibido_en,
        ))


@dataclass(slots=True, frozen=True)
class VistaBufferLittleFS:
    """Lo que el tablero muestra del buffer de un nodo (HU-48 esc. 1-3)."""

    pendientes: int
    limite: int
    ocupacion_pct: int | None
    desde: datetime | None
    hasta: datetime | None
    rango_estimado: bool
    perdidas: int
    cuarentena: int | None
    reportado_en: datetime | None
    ultima_sincronizacion_en: datetime | None
    brechas: list[EpisodioBufferSaturado] = field(default_factory=list)


def construir_vista_buffer(
    estado: EstadoNodo | None,
    episodios: list[EpisodioBufferSaturado],
    ultima_captura: datetime | None,
) -> VistaBufferLittleFS | None:
    """None si el nodo nunca informó su buffer ni tuvo brechas (firmware
    anterior a `estado_nodo`): el tablero no muestra un buffer inventado.

    Rango de los pendientes: el que informe el firmware; si no lo informa, se
    estima. La cola es FIFO y lo acusado ya está en la base, así que los
    pendientes son posteriores a la última lectura persistida y anteriores al
    latido (`rango_estimado = true`)."""
    if estado is None and not episodios:
        return None
    pendientes = (estado.pendientes if estado else None) or 0
    desde = hasta = None
    estimado = False
    if estado is not None and pendientes > 0:
        desde, hasta = _utc(estado.pendientes_desde), _utc(estado.pendientes_hasta)
        if desde is None or hasta is None:
            estimado = True
            hasta = hasta or _utc(estado.ocurrido_en) or _utc(estado.recibido_en)
            desde = desde or ultima_captura
            if desde is not None and hasta is not None and desde > hasta:
                desde = hasta
    perdidas_episodios = sum(e.perdidas for e in episodios)
    return VistaBufferLittleFS(
        pendientes=pendientes,
        limite=(estado.capacidad if estado else None) or CAPACIDAD_BUFFER_POR_DEFECTO,
        ocupacion_pct=estado.ocupacion_pct if estado else None,
        desde=desde,
        hasta=hasta,
        rango_estimado=estimado,
        # El contador del nodo es persistente; si aún no llegó, lo que suman
        # los episodios conocidos.
        perdidas=(estado.perdidas_total if estado and estado.perdidas_total is not None else perdidas_episodios),
        cuarentena=estado.cuarentena if estado else None,
        reportado_en=_utc(estado.recibido_en) if estado else None,
        ultima_sincronizacion_en=_utc(estado.ultima_sincronizacion_en) if estado else None,
        brechas=episodios,
    )

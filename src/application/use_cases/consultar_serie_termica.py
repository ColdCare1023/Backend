"""HU-31 / HU-36: serie térmica agregada de un dispositivo en un periodo.

La curva y el resumen del periodo salen de agregados SQL (mínimo, máximo,
promedio y conteo por intervalo), no de la lista de lecturas crudas con tope:
24 h a 10 s por lectura son 8640 filas y 7 días a 30 s, 20 160, y ambos
periodos deben dibujarse completos. El tamaño del intervalo se elige para que
la curva tenga a lo sumo `MAX_BUCKETS_AUTO` puntos; el mínimo y el máximo de
cada uno conservan los picos (una excursión breve no se promedia).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.domain.entities.serie_termica import BrechaSerie, SerieTermica
from src.domain.exceptions import DomainError
from src.domain.repositories.i_estado_nodo_repository import EpisodioBufferSaturado, IEstadoNodoRepository
from src.domain.repositories.i_lectura_repository import ILecturaRepository

# HU-31 esc. 4: sin rango, las últimas 24 horas.
VENTANA_POR_DEFECTO = timedelta(hours=24)
# Un año: la agregación acota la respuesta, no el periodo.
RANGO_MAXIMO = timedelta(days=366)
# Intervalos que elige el modo automático (múltiplos "redondos" para el eje).
BUCKETS_AUTOMATICOS = (60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 21600, 43200, 86400)
MAX_BUCKETS_AUTO = 1500
# Con un intervalo explícito, tope de puntos para no devolver una serie cruda
# disfrazada de agregada.
MAX_BUCKETS_MANUAL = 5000
BUCKET_MINIMO = 10
BUCKET_MAXIMO = 86400
MAX_BRECHAS = 500

CAUSA_SIN_DATOS = "sin_datos"
CAUSA_BUFFER_AGOTADO = "buffer_agotado"


class RangoSerieInvalidoError(DomainError):
    """Periodo o intervalo de agregación no admitidos (422)."""


def _utc(instante: datetime) -> datetime:
    """Sin zona se interpreta como UTC; con otra zona se convierte (SQLite
    compara la hora como texto UTC)."""
    return instante.astimezone(timezone.utc) if instante.tzinfo else instante.replace(tzinfo=timezone.utc)


def normalizar_rango(
    desde: datetime | None, hasta: datetime | None, ahora: datetime
) -> tuple[datetime, datetime]:
    """Rango efectivo: sin `hasta`, ahora; sin `desde`, 24 h antes de `hasta`.
    HU-36 esc. 3: un rango invertido se rechaza también en el backend."""
    hasta = _utc(hasta) if hasta is not None else ahora
    desde = _utc(desde) if desde is not None else hasta - VENTANA_POR_DEFECTO
    if desde > hasta:
        raise RangoSerieInvalidoError("desde no puede ser posterior a hasta")
    if hasta - desde > RANGO_MAXIMO:
        raise RangoSerieInvalidoError(f"El periodo no puede superar {RANGO_MAXIMO.days} días")
    return desde, hasta


def elegir_bucket(desde: datetime, hasta: datetime, solicitado: int | None = None) -> int:
    """Segundos por intervalo. `solicitado=None` es el modo automático: el
    menor de `BUCKETS_AUTOMATICOS` que deja la curva en `MAX_BUCKETS_AUTO`
    puntos o menos (24 h → 1 min; 7 d → 10 min; 30 d → 30 min)."""
    segundos = max(1.0, (hasta - desde).total_seconds())
    if solicitado is None:
        for candidato in BUCKETS_AUTOMATICOS:
            if segundos / candidato <= MAX_BUCKETS_AUTO:
                return candidato
        return BUCKETS_AUTOMATICOS[-1]
    if not BUCKET_MINIMO <= solicitado <= BUCKET_MAXIMO:
        raise RangoSerieInvalidoError(
            f"El intervalo de agregación debe estar entre {BUCKET_MINIMO} y {BUCKET_MAXIMO} segundos"
        )
    if segundos / solicitado > MAX_BUCKETS_MANUAL:
        raise RangoSerieInvalidoError(
            f"El intervalo de {solicitado} s produce más de {MAX_BUCKETS_MANUAL} puntos para este periodo; "
            "use uno mayor o bucket=auto"
        )
    return solicitado


def _se_solapan(brecha_desde: datetime, brecha_hasta: datetime, episodio: EpisodioBufferSaturado) -> bool:
    inicio = _utc(episodio.desde) if episodio.desde else None
    fin = _utc(episodio.hasta) if episodio.hasta else None
    if inicio is None and fin is None:
        return False
    return (inicio is None or inicio <= brecha_hasta) and (fin is None or fin >= brecha_desde)


def clasificar_brechas(
    huecos: list[tuple[datetime, datetime, bool]], episodios: list[EpisodioBufferSaturado]
) -> list[BrechaSerie]:
    """HU-48 esc. 2: una brecha que coincide con un episodio de buffer
    saturado tiene causa conocida (capacidad de LittleFS agotada) y las
    muestras que el nodo no pudo almacenar; el resto es ausencia de datos."""
    brechas = []
    for desde, hasta, abierta in huecos:
        coinciden = [e for e in episodios if _se_solapan(desde, hasta, e)]
        brechas.append(BrechaSerie(
            desde=desde,
            hasta=hasta,
            causa=CAUSA_BUFFER_AGOTADO if coinciden else CAUSA_SIN_DATOS,
            perdidas=sum(e.perdidas for e in coinciden) if coinciden else None,
            abierta=abierta,
        ))
    return brechas


class ConsultarSerieTermicaUseCase:
    def __init__(
        self,
        lectura_repository: ILecturaRepository,
        estado_nodo_repository: IEstadoNodoRepository | None = None,
    ) -> None:
        self._lecturas = lectura_repository
        self._estado_nodo = estado_nodo_repository

    async def execute(
        self,
        device_id: str,
        desde: datetime | None,
        hasta: datetime | None,
        *,
        intervalo_reporte_segundos: int,
        n_intervalos_sin_datos: int,
        bucket_segundos: int | None = None,
        registrado_en: datetime | None = None,
        ahora: datetime | None = None,
    ) -> SerieTermica:
        ahora = ahora or datetime.now(timezone.utc)
        desde, hasta = normalizar_rango(desde, hasta, ahora)
        bucket = elegir_bucket(desde, hasta, bucket_segundos)
        # HU-54: el mismo umbral que declara SIN_DATOS (N x intervalo del nodo).
        umbral = max(1, intervalo_reporte_segundos) * n_intervalos_sin_datos

        buckets = await self._lecturas.agregar_serie(device_id, desde, hasta, bucket)
        resumen = await self._lecturas.resumir_periodo(device_id, desde, hasta)

        huecos: list[tuple[datetime, datetime, bool]] = []
        truncadas = False
        if resumen.lecturas:
            internos = await self._lecturas.detectar_huecos(device_id, desde, hasta, umbral, MAX_BRECHAS + 1)
            truncadas = len(internos) > MAX_BRECHAS
            # Bordes del periodo: antes de la primera lectura (sin contar el
            # tiempo previo al alta del equipo) y después de la última (hasta
            # ahora, si el periodo llega al presente: brecha en curso).
            inicio_util = max(desde, _utc(registrado_en)) if registrado_en else desde
            primera, ultima = resumen.primera_lectura_en, resumen.ultima_lectura_en
            if primera is not None and (primera - inicio_util).total_seconds() > umbral:
                huecos.append((inicio_util, primera, False))
            huecos.extend((a, b, False) for a, b in internos[:MAX_BRECHAS])
            fin_util = min(hasta, ahora)
            if ultima is not None and (fin_util - ultima).total_seconds() > umbral:
                huecos.append((ultima, fin_util, hasta >= ahora))

        episodios: list[EpisodioBufferSaturado] = []
        if huecos and self._estado_nodo is not None:
            episodios = await self._estado_nodo.listar_episodios(device_id, limite=200, desde=desde, hasta=hasta)

        return SerieTermica(
            device_id=device_id,
            desde=desde,
            hasta=hasta,
            bucket_segundos=bucket,
            bucket_automatico=bucket_segundos is None,
            intervalo_reporte_segundos=intervalo_reporte_segundos,
            umbral_brecha_segundos=umbral,
            buckets=buckets,
            resumen=resumen,
            brechas=clasificar_brechas(huecos, episodios),
            brechas_truncadas=truncadas,
        )

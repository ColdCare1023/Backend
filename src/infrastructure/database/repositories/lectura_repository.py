from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import BigInteger, Integer, and_, case, cast, extract, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.lectura_termica import ORIGEN_DISPOSITIVO, ORIGEN_MANUAL, LecturaTermica
from src.domain.entities.serie_termica import BucketSerie, EstadisticaMagnitud, ResumenSerie
from src.domain.repositories.i_lectura_repository import ILecturaRepository
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.domain.value_objects.rango_termico import RANGO_TERMICO_BPA
from src.infrastructure.database.models import DeviceModel, ThermalReadingModel

_T = ThermalReadingModel
# HU-31/HU-07: hora de captura de la lectura. `timestamp_estimado` es la hora
# reconstruida de una lectura `unsynced` cuando su arranque sincronizó.
_HORA_CAPTURA = func.coalesce(_T.timestamp_estimado, _T.timestamp)
# Lectura `unsynced` sin reanclar: su `timestamp` es la hora de LLEGADA al
# backend, no la de captura. No se puede ubicar en la curva.
_SIN_HORA = and_(_T.time_quality == "unsynced", _T.timestamp_estimado.is_(None))
_CON_HORA = or_(_T.time_quality.is_(None), _T.time_quality != "unsynced", _T.timestamp_estimado.is_not(None))
_FUERA_DE_RANGO = or_(
    _T.temperatura_interna < RANGO_TERMICO_BPA.minimo_celsius,
    _T.temperatura_interna > RANGO_TERMICO_BPA.maximo_celsius,
)


def _como_utc(instante: datetime | str | None) -> datetime | None:
    """SQLite devuelve la hora sin zona (o como texto en expresiones
    agregadas); todas las horas se guardan en UTC."""
    if instante is None:
        return None
    if isinstance(instante, str):
        instante = datetime.fromisoformat(instante)
    return instante if instante.tzinfo else instante.replace(tzinfo=timezone.utc)


def _real(valor) -> float | None:
    return None if valor is None else round(float(valor), 3)


def _estadistica(fila, prefijo: str) -> EstadisticaMagnitud:
    return EstadisticaMagnitud(
        minimo=_real(getattr(fila, f"{prefijo}_min")),
        maximo=_real(getattr(fila, f"{prefijo}_max")),
        promedio=_real(getattr(fila, f"{prefijo}_avg")),
        n=int(getattr(fila, f"{prefijo}_n") or 0),
    )


def _agregados_magnitudes() -> list:
    columnas = []
    for prefijo, columna in (
        ("ti", _T.temperatura_interna), ("ta", _T.temperatura_ambiental), ("h", _T.humedad_ambiental)
    ):
        columnas += [
            func.min(columna).label(f"{prefijo}_min"),
            func.max(columna).label(f"{prefijo}_max"),
            func.avg(columna).label(f"{prefijo}_avg"),
            func.count(columna).label(f"{prefijo}_n"),
        ]
    return columnas


def _to_entity(model: ThermalReadingModel) -> LecturaTermica:
    return LecturaTermica(
        id=model.id,
        device_id=model.device_id,
        timestamp=model.timestamp,
        temperatura_ambiental=model.temperatura_ambiental,
        humedad_ambiental=model.humedad_ambiental,
        temperatura_interna=model.temperatura_interna,
        apertura_refrigerador=model.apertura_refrigerador,
        estado_conectividad=model.estado_conectividad or "offline",
        nivel_riesgo=NivelRiesgo(model.nivel_riesgo) if model.nivel_riesgo else None,
        payload=model.payload,
        modelo_version=model.modelo_version,
        confianza_ia=model.confianza_ia,
        origen_clasificacion=model.origen_clasificacion,
        estado_inferencia=model.estado_inferencia,
        inferido_en=model.inferido_en,
        motivo_no_inferencia=model.motivo_no_inferencia,
        reading_id=model.reading_id,
        schema_version=model.schema_version,
        excursion_confirmada=model.excursion_confirmada,
        riesgo_efectivo=NivelRiesgo(model.riesgo_efectivo) if model.riesgo_efectivo else None,
        probabilidades_ia=model.probabilidades_ia,
        vector_features_ia=model.vector_features_ia,
        boot_id=model.boot_id,
        seq_no=model.seq_no,
        time_quality=model.time_quality,
        received_at=model.received_at,
        uptime_ms=model.uptime_ms,
        timestamp_estimado=model.timestamp_estimado,
        origen=model.origen or ORIGEN_DISPOSITIVO,
        registrado_por=model.registrado_por,
    )


class SQLAlchemyLecturaRepository(ILecturaRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _asegurar_device(self, device_id: str) -> None:
        existente = await self._session.get(DeviceModel, device_id)
        if existente is None:
            self._session.add(DeviceModel(id=device_id))
            await self._session.flush()

    async def agregar(self, lectura: LecturaTermica) -> LecturaTermica:
        await self._asegurar_device(lectura.device_id)
        model = ThermalReadingModel(
            device_id=lectura.device_id,
            timestamp=lectura.timestamp,
            temperatura_ambiental=lectura.temperatura_ambiental,
            humedad_ambiental=lectura.humedad_ambiental,
            temperatura_interna=lectura.temperatura_interna,
            apertura_refrigerador=lectura.apertura_refrigerador,
            nivel_riesgo=lectura.nivel_riesgo.value if lectura.nivel_riesgo else None,
            estado_conectividad=lectura.estado_conectividad,
            payload=lectura.payload,
            modelo_version=lectura.modelo_version,
            confianza_ia=lectura.confianza_ia,
            origen_clasificacion=lectura.origen_clasificacion,
            estado_inferencia=lectura.estado_inferencia,
            inferido_en=lectura.inferido_en,
            motivo_no_inferencia=lectura.motivo_no_inferencia,
            reading_id=lectura.reading_id,
            schema_version=lectura.schema_version,
            excursion_confirmada=lectura.excursion_confirmada,
            riesgo_efectivo=lectura.riesgo_efectivo.value if lectura.riesgo_efectivo else None,
            probabilidades_ia=lectura.probabilidades_ia,
            vector_features_ia=lectura.vector_features_ia,
            boot_id=lectura.boot_id,
            seq_no=lectura.seq_no,
            time_quality=lectura.time_quality,
            received_at=lectura.received_at,
            uptime_ms=lectura.uptime_ms,
            origen=lectura.origen,
            registrado_por=lectura.registrado_por,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_entity(model)

    async def agregar_si_no_existe(self, lectura: LecturaTermica) -> tuple[LecturaTermica, bool]:
        """HU-11/HU-22 (B-9): la consulta previa del caso de uso no basta con
        dos consumidores o un reenvío QoS 1 simultáneo: ambos ven «no existe».
        En PostgreSQL la decide el índice único (device_id, boot_id, seq_no) o
        (device_id, timestamp): la inserción va en un SAVEPOINT y, si el índice
        la rechaza, se devuelve la lectura que ganó, sin abortar la transacción."""
        if lectura.es_manual or self._session.get_bind().dialect.name != "postgresql":
            return await self.agregar(lectura), True
        try:
            async with self._session.begin_nested():
                creada = await self.agregar(lectura)
            return creada, True
        except IntegrityError:
            if lectura.boot_id is not None and lectura.seq_no is not None:
                existente = await self.obtener_por_device_boot_seq(lectura.device_id, lectura.boot_id, lectura.seq_no)
            else:
                existente = await self.obtener_por_device_y_timestamp(lectura.device_id, lectura.timestamp)
            if existente is None:
                raise
            return existente, False

    async def obtener_por_id(self, lectura_id: UUID) -> LecturaTermica | None:
        model = await self._session.get(ThermalReadingModel, lectura_id)
        return _to_entity(model) if model else None

    async def listar(
        self,
        device_id: str | None = None,
        nivel_riesgo: str | None = None,
        estado_conectividad: str | None = None,
        riesgo_efectivo: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limite: int = 100,
        offset: int = 0,
    ) -> list[LecturaTermica]:
        stmt = select(ThermalReadingModel)
        if device_id:
            stmt = stmt.where(ThermalReadingModel.device_id == device_id)
        if nivel_riesgo:
            stmt = stmt.where(ThermalReadingModel.nivel_riesgo == nivel_riesgo)
        if estado_conectividad:
            stmt = stmt.where(ThermalReadingModel.estado_conectividad == estado_conectividad)
        if riesgo_efectivo:
            # HU-36: el filtro debe coincidir con lo que muestra la tabla (riesgo efectivo).
            stmt = stmt.where(ThermalReadingModel.riesgo_efectivo == riesgo_efectivo)
        if desde:
            stmt = stmt.where(ThermalReadingModel.timestamp >= desde)
        if hasta:
            stmt = stmt.where(ThermalReadingModel.timestamp <= hasta)
        stmt = stmt.order_by(ThermalReadingModel.timestamp.desc()).limit(limite).offset(offset)
        result = await self._session.execute(stmt)
        return [_to_entity(m) for m in result.scalars().all()]

    async def ultima_recibida_por_device(self, device_id: str) -> LecturaTermica | None:
        """Última lectura que LLEGÓ al backend (por `received_at`, no por la hora
        declarada): es la que dice si el nodo sigue reportando."""
        stmt = (
            select(ThermalReadingModel)
            .where(
                ThermalReadingModel.device_id == device_id,
                # Una lectura manual no demuestra que el nodo siga reportando.
                ThermalReadingModel.origen == ORIGEN_DISPOSITIVO,
            )
            .order_by(ThermalReadingModel.received_at.desc().nulls_last(), ThermalReadingModel.timestamp.desc())
            .limit(1)
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(model) if model else None

    async def listar_recientes_por_device(
        self, device_id: str, limite: int, solo_dispositivo: bool = True
    ) -> list[LecturaTermica]:
        stmt = select(ThermalReadingModel).where(ThermalReadingModel.device_id == device_id)
        if solo_dispositivo:
            # Las features de la IA para la telemetría del nodo salen solo de
            # la telemetría del nodo: una carga manual no debe poder sesgar la
            # clasificación de las lecturas reales.
            stmt = stmt.where(ThermalReadingModel.origen == ORIGEN_DISPOSITIVO)
        stmt = stmt.order_by(ThermalReadingModel.timestamp.desc()).limit(limite)
        result = await self._session.execute(stmt)
        return [_to_entity(m) for m in result.scalars().all()]

    async def obtener_por_device_y_timestamp(
        self, device_id: str, timestamp: datetime
    ) -> LecturaTermica | None:
        # BE-024: desde la migración 0015, (device_id, timestamp) solo es único
        # entre lecturas SIN boot_id/seq_no (índice parcial de 0017). Las del
        # firmware nuevo pueden repetir marca de tiempo (sin NTP), y comparar
        # contra ellas producía MultipleResultsFound (500 / mensaje MQTT
        # descartado). La identidad heredada solo se compara con lecturas
        # heredadas, que es justo el conjunto que el índice mantiene único.
        stmt = select(ThermalReadingModel).where(
            ThermalReadingModel.device_id == device_id,
            ThermalReadingModel.timestamp == timestamp,
            ThermalReadingModel.origen == ORIGEN_DISPOSITIVO,
            or_(ThermalReadingModel.boot_id.is_(None), ThermalReadingModel.seq_no.is_(None)),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_entity(model) if model else None

    async def reanclar_no_sincronizadas(self, device_id: str, boot_id: int) -> int:
        """Reconstruye la hora de las lecturas `unsynced` de un arranque.

        Dentro de un arranque, `uptime_ms` avanza con el mismo reloj que el
        timestamp, así que una sola lectura `synced` del arranque fija la
        hora real del instante 0: `timestamp - uptime_ms`. El resultado va a
        `timestamp_estimado`; `timestamp` no se toca. Devuelve cuántas filas
        reancló (0 si el arranque aún no tiene ninguna lectura sincronizada).
        """
        ancla = (
            await self._session.execute(
                select(ThermalReadingModel.timestamp, ThermalReadingModel.uptime_ms)
                .where(
                    ThermalReadingModel.device_id == device_id,
                    ThermalReadingModel.boot_id == boot_id,
                    ThermalReadingModel.time_quality == "synced",
                    ThermalReadingModel.uptime_ms.is_not(None),
                )
                .order_by(ThermalReadingModel.seq_no)
                .limit(1)
            )
        ).first()
        if ancla is None:
            return 0
        inicio_del_arranque = ancla.timestamp - timedelta(milliseconds=ancla.uptime_ms)
        pendientes = (
            await self._session.execute(
                select(ThermalReadingModel).where(
                    ThermalReadingModel.device_id == device_id,
                    ThermalReadingModel.boot_id == boot_id,
                    ThermalReadingModel.time_quality == "unsynced",
                    ThermalReadingModel.uptime_ms.is_not(None),
                    ThermalReadingModel.timestamp_estimado.is_(None),
                )
            )
        ).scalars().all()
        for fila in pendientes:
            fila.timestamp_estimado = inicio_del_arranque + timedelta(milliseconds=fila.uptime_ms)
        await self._session.flush()
        return len(pendientes)

    async def obtener_sincronizada_anterior(
        self, device_id: str, boot_id: int, seq_no: int
    ) -> LecturaTermica | None:
        """IOT-007: lectura sincronizada más reciente del mismo arranque, antes
        de `seq_no` (para contrastar la base época↔uptime)."""
        stmt = (
            select(ThermalReadingModel)
            .where(
                ThermalReadingModel.device_id == device_id,
                ThermalReadingModel.boot_id == boot_id,
                ThermalReadingModel.seq_no < seq_no,
                ThermalReadingModel.time_quality == "synced",
                ThermalReadingModel.origen == ORIGEN_DISPOSITIVO,
            )
            .order_by(ThermalReadingModel.seq_no.desc())
            .limit(1)
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(model) if model else None

    async def obtener_por_device_boot_seq(
        self, device_id: str, boot_id: int, seq_no: int
    ) -> LecturaTermica | None:
        stmt = select(ThermalReadingModel).where(
            ThermalReadingModel.device_id == device_id,
            ThermalReadingModel.boot_id == boot_id,
            ThermalReadingModel.seq_no == seq_no,
            ThermalReadingModel.origen == ORIGEN_DISPOSITIVO,
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_entity(model) if model else None

    async def estadisticas_periodo(
        self,
        device_id: str | None,
        desde: datetime | None,
        hasta: datetime | None,
        minimo_celsius: float,
        maximo_celsius: float,
    ) -> dict:
        """HU-38: agregados SQL sobre el periodo completo, con los mismos
        filtros que `listar` (por `timestamp` de captura) pero sin su tope."""
        filtros = []
        if device_id:
            filtros.append(ThermalReadingModel.device_id == device_id)
        if desde:
            filtros.append(ThermalReadingModel.timestamp >= desde)
        if hasta:
            filtros.append(ThermalReadingModel.timestamp <= hasta)
        temperatura = ThermalReadingModel.temperatura_interna
        en_rango = func.coalesce(
            func.sum(case((and_(temperatura >= minimo_celsius, temperatura <= maximo_celsius), 1), else_=0)), 0
        )
        total, con_temperatura, conformes, minima, maxima, promedio = (
            await self._session.execute(
                select(
                    func.count(ThermalReadingModel.id),
                    func.count(temperatura),
                    en_rango,
                    func.min(temperatura),
                    func.max(temperatura),
                    func.avg(temperatura),
                ).where(*filtros)
            )
        ).one()
        # Mismo criterio que el detalle: riesgo efectivo, o la clase del modelo
        # en lecturas antiguas que no lo tienen calculado.
        nivel = func.coalesce(
            ThermalReadingModel.riesgo_efectivo, ThermalReadingModel.nivel_riesgo, "sin_clasificar"
        )
        consulta_niveles = select(nivel, func.count(ThermalReadingModel.id)).where(*filtros).group_by(nivel)
        por_nivel = {
            str(clave): int(cantidad) for clave, cantidad in (await self._session.execute(consulta_niveles)).all()
        }
        return {
            "total_lecturas": int(total),
            "lecturas_con_temperatura": int(con_temperatura),
            "lecturas_en_rango": int(conformes),
            "temp_minima": minima,
            "temp_maxima": maxima,
            "temp_promedio": float(promedio) if promedio is not None else None,
            "por_nivel": por_nivel,
        }

    # ── HU-31 / HU-36: serie agregada ─────────────────────────────────────

    def _dialecto(self) -> str:
        return self._session.get_bind().dialect.name

    def _epoca(self, expresion):
        """Segundos UNIX de una hora. En SQLite la hora se guarda como texto
        UTC sin zona; `strftime('%s')` la convierte."""
        if self._dialecto() == "postgresql":
            return extract("epoch", expresion)
        return cast(func.strftime("%s", expresion), Integer)

    def _indice_bucket(self, bucket_segundos: int):
        """Número de intervalo de la lectura: piso(época / bucket). En
        PostgreSQL la época es numérica y CAST redondea, de ahí el `floor`; en
        SQLite es entera y la división entera ya es el piso (no depende de que
        SQLite tenga las funciones matemáticas compiladas)."""
        epoca = self._epoca(_HORA_CAPTURA)
        if self._dialecto() == "postgresql":
            return cast(func.floor(epoca / bucket_segundos), BigInteger)
        return epoca // bucket_segundos

    def _en_periodo(self, device_id: str, desde: datetime, hasta: datetime) -> list:
        """Filtro por hora de captura. La hora reconstruida nunca es posterior
        a la de llegada (`timestamp` de una `unsynced`), así que
        `timestamp >= desde` es una condición necesaria que sí usa el índice
        (device_id, timestamp)."""
        return [
            _T.device_id == device_id,
            _T.timestamp >= desde,
            _HORA_CAPTURA >= desde,
            _HORA_CAPTURA <= hasta,
        ]

    async def contar(
        self,
        device_id: str | None = None,
        nivel_riesgo: str | None = None,
        estado_conectividad: str | None = None,
        riesgo_efectivo: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(_T)
        if device_id:
            stmt = stmt.where(_T.device_id == device_id)
        if nivel_riesgo:
            stmt = stmt.where(_T.nivel_riesgo == nivel_riesgo)
        if estado_conectividad:
            stmt = stmt.where(_T.estado_conectividad == estado_conectividad)
        if riesgo_efectivo:
            stmt = stmt.where(_T.riesgo_efectivo == riesgo_efectivo)
        if desde:
            stmt = stmt.where(_T.timestamp >= desde)
        if hasta:
            stmt = stmt.where(_T.timestamp <= hasta)
        return int((await self._session.execute(stmt)).scalar_one())

    async def agregar_serie(
        self, device_id: str, desde: datetime, hasta: datetime, bucket_segundos: int
    ) -> list[BucketSerie]:
        indice = self._indice_bucket(bucket_segundos).label("indice")
        stmt = (
            select(
                indice,
                func.count().label("lecturas"),
                *_agregados_magnitudes(),
                func.sum(case((_FUERA_DE_RANGO, 1), else_=0)).label("fuera"),
                func.sum(case((_T.excursion_confirmada.is_(True), 1), else_=0)).label("excursiones"),
            )
            .where(*self._en_periodo(device_id, desde, hasta), _T.origen == ORIGEN_DISPOSITIVO, _CON_HORA)
            .group_by(indice)
            .order_by(indice)
        )
        buckets = []
        for fila in (await self._session.execute(stmt)).all():
            inicio = datetime.fromtimestamp(int(fila.indice) * bucket_segundos, tz=timezone.utc)
            buckets.append(BucketSerie(
                inicio=inicio,
                fin=inicio + timedelta(seconds=bucket_segundos),
                lecturas=int(fila.lecturas),
                temperatura_interna=_estadistica(fila, "ti"),
                temperatura_ambiental=_estadistica(fila, "ta"),
                humedad_ambiental=_estadistica(fila, "h"),
                fuera_de_rango=int(fila.fuera or 0),
                excursiones=int(fila.excursiones or 0),
            ))
        return buckets

    async def resumir_periodo(self, device_id: str, desde: datetime, hasta: datetime) -> ResumenSerie:
        de_la_serie = and_(_T.origen == ORIGEN_DISPOSITIVO, _CON_HORA)
        en_periodo = self._en_periodo(device_id, desde, hasta)
        stmt = select(
            func.count().label("lecturas"),
            *_agregados_magnitudes(),
            func.sum(case((_FUERA_DE_RANGO, 1), else_=0)).label("fuera"),
            func.sum(case((_T.excursion_confirmada.is_(True), 1), else_=0)).label("excursiones"),
            func.min(_HORA_CAPTURA).label("primera"),
            func.max(_HORA_CAPTURA).label("ultima"),
        ).where(*en_periodo, de_la_serie)
        fila = (await self._session.execute(stmt)).one()
        # Sin hora y manuales: su hora (llegada o la declarada por la persona)
        # es `timestamp`, que aquí coincide con la hora de captura del filtro.
        apartadas = (
            await self._session.execute(
                select(
                    func.sum(case((and_(_T.origen == ORIGEN_DISPOSITIVO, _SIN_HORA), 1), else_=0)),
                    func.sum(case((_T.origen == ORIGEN_MANUAL, 1), else_=0)),
                ).where(*en_periodo)
            )
        ).one()
        por_riesgo = {
            (riesgo or "sin_clasificar"): int(n)
            for riesgo, n in (
                await self._session.execute(
                    select(_T.riesgo_efectivo, func.count()).where(*en_periodo, de_la_serie).group_by(
                        _T.riesgo_efectivo
                    )
                )
            ).all()
        }
        return ResumenSerie(
            lecturas=int(fila.lecturas or 0),
            temperatura_interna=_estadistica(fila, "ti"),
            temperatura_ambiental=_estadistica(fila, "ta"),
            humedad_ambiental=_estadistica(fila, "h"),
            fuera_de_rango=int(fila.fuera or 0),
            excursiones=int(fila.excursiones or 0),
            primera_lectura_en=_como_utc(fila.primera),
            ultima_lectura_en=_como_utc(fila.ultima),
            lecturas_sin_hora=int(apartadas[0] or 0),
            lecturas_manuales=int(apartadas[1] or 0),
            por_riesgo_efectivo=por_riesgo,
        )

    async def detectar_huecos(
        self, device_id: str, desde: datetime, hasta: datetime, umbral_segundos: int, limite: int
    ) -> list[tuple[datetime, datetime]]:
        hora = _HORA_CAPTURA.label("hora")
        epoca = self._epoca(_HORA_CAPTURA)
        consecutivas = (
            select(
                hora,
                func.lag(_HORA_CAPTURA).over(order_by=_HORA_CAPTURA).label("anterior"),
                (epoca - func.lag(epoca).over(order_by=_HORA_CAPTURA)).label("salto"),
            )
            .where(*self._en_periodo(device_id, desde, hasta), _T.origen == ORIGEN_DISPOSITIVO, _CON_HORA)
            .subquery()
        )
        stmt = (
            select(consecutivas.c.anterior, consecutivas.c.hora)
            .where(consecutivas.c.salto > umbral_segundos)
            .order_by(consecutivas.c.hora)
            .limit(limite)
        )
        return [
            (_como_utc(anterior), _como_utc(siguiente))
            for anterior, siguiente in (await self._session.execute(stmt)).all()
        ]

    async def ultima_hora_de_captura(self, device_id: str) -> datetime | None:
        stmt = select(func.max(_HORA_CAPTURA)).where(
            _T.device_id == device_id, _T.origen == ORIGEN_DISPOSITIVO, _CON_HORA
        )
        return _como_utc((await self._session.execute(stmt)).scalar_one_or_none())

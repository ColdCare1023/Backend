from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.alerta_termica import AlertaTermica
from src.domain.repositories.i_alerta_repository import IAlertaRepository
from src.domain.value_objects.estado_alerta import EstadoAlerta
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.database.models import ThermalAlertModel, ThermalReadingModel


def _to_entity(model: ThermalAlertModel) -> AlertaTermica:
    return AlertaTermica(
        id=model.id,
        reading_id=model.reading_id,
        device_id=model.device_id,
        nivel_riesgo=NivelRiesgo(model.nivel_riesgo),
        mensaje=model.mensaje,
        revisada=model.revisada,
        revisada_por=model.revisada_por,
        created_at=model.created_at,
        episodio_abierto=model.episodio_abierto == 1,
        lectura_inicial_id=model.lectura_inicial_id,
        lectura_mas_reciente_id=model.lectura_mas_reciente_id,
        ultima_actualizacion=model.ultima_actualizacion,
        cerrada_en=model.cerrada_en,
        estado=EstadoAlerta(model.estado),
        reconocida_en=model.reconocida_en,
        atendida_en=model.atendida_en,
        notificada_en=model.notificada_en,
        notificacion_intentos=model.notificacion_intentos or 0,
        iniciada_en=model.iniciada_en,
        temperatura_pico=model.temperatura_pico,
        historica=bool(model.historica),
        normalizacion_desde=model.normalizacion_desde,
    )


class SQLAlchemyAlertaRepository(IAlertaRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def agregar(self, alerta: AlertaTermica) -> AlertaTermica:
        model = ThermalAlertModel(
            reading_id=alerta.reading_id,
            device_id=alerta.device_id,
            nivel_riesgo=alerta.nivel_riesgo.value,
            mensaje=alerta.mensaje,
            revisada=alerta.revisada,
            revisada_por=alerta.revisada_por,
            episodio_abierto=1 if alerta.episodio_abierto else None,
            lectura_inicial_id=alerta.lectura_inicial_id or alerta.reading_id,
            lectura_mas_reciente_id=alerta.lectura_mas_reciente_id or alerta.reading_id,
            ultima_actualizacion=alerta.ultima_actualizacion,
            cerrada_en=alerta.cerrada_en,
            estado=alerta.estado.value,
            reconocida_en=alerta.reconocida_en,
            atendida_en=alerta.atendida_en,
            iniciada_en=alerta.iniciada_en,
            temperatura_pico=alerta.temperatura_pico,
            historica=alerta.historica,
            normalizacion_desde=alerta.normalizacion_desde,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_entity(model)

    async def obtener_por_id(self, alerta_id: UUID) -> AlertaTermica | None:
        model = await self._session.get(ThermalAlertModel, alerta_id)
        return _to_entity(model) if model else None

    async def listar(
        self,
        device_id: str | None = None,
        revisada: bool | None = None,
        estado: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limite: int = 100,
        offset: int = 0,
    ) -> list[AlertaTermica]:
        stmt = select(ThermalAlertModel)
        if device_id:
            stmt = stmt.where(ThermalAlertModel.device_id == device_id)
        if revisada is not None:
            stmt = stmt.where(ThermalAlertModel.revisada == revisada)
        if estado is not None:
            stmt = stmt.where(ThermalAlertModel.estado == estado)
        # Acotar por periodo es indispensable en el reporte BPA (RF-13): sin
        # esto el reporte de un mes incluía las alertas de todo el histórico.
        if desde is not None:
            stmt = stmt.where(ThermalAlertModel.created_at >= desde)
        if hasta is not None:
            stmt = stmt.where(ThermalAlertModel.created_at <= hasta)
        stmt = stmt.order_by(ThermalAlertModel.created_at.desc()).limit(limite).offset(offset)
        result = await self._session.execute(stmt)
        return [_to_entity(m) for m in result.scalars().all()]

    async def actualizar(self, alerta: AlertaTermica) -> AlertaTermica:
        model = await self._session.get(ThermalAlertModel, alerta.id)
        if model is None:
            raise ValueError(f"Alerta {alerta.id} no encontrada")
        model.revisada = alerta.revisada
        model.revisada_por = alerta.revisada_por
        model.episodio_abierto = 1 if alerta.episodio_abierto else None
        model.lectura_mas_reciente_id = alerta.lectura_mas_reciente_id
        model.ultima_actualizacion = alerta.ultima_actualizacion
        model.cerrada_en = alerta.cerrada_en
        model.estado = alerta.estado.value
        model.reconocida_en = alerta.reconocida_en
        model.atendida_en = alerta.atendida_en
        model.iniciada_en = alerta.iniciada_en
        model.temperatura_pico = alerta.temperatura_pico
        model.historica = alerta.historica
        model.normalizacion_desde = alerta.normalizacion_desde
        await self._session.flush()
        await self._session.refresh(model)
        return _to_entity(model)

    async def marcar_atendida_si_no_atendida(self, alerta_id: UUID, timestamp: datetime) -> bool:
        """HU-23 esc. 3: solo RECONOCIDA -> ATENDIDA (nunca desde PENDIENTE)."""
        stmt = (
            update(ThermalAlertModel)
            .where(ThermalAlertModel.id == alerta_id, ThermalAlertModel.estado == EstadoAlerta.RECONOCIDA.value)
            .values(estado=EstadoAlerta.ATENDIDA.value, atendida_en=timestamp)
        )
        resultado = await self._session.execute(stmt)
        await self._session.flush()
        return resultado.rowcount == 1

    async def reconocer_si_pendiente(self, alerta_id: UUID, usuario_id: UUID, timestamp: datetime) -> bool:
        """HU-23 esc. 1, atómico: PENDIENTE -> RECONOCIDA con quién y cuándo.
        En PostgreSQL el UPDATE bloquea la fila hasta el COMMIT, así que una
        acción concurrente sobre la misma alerta espera y luego no la encuentra
        PENDIENTE."""
        stmt = (
            update(ThermalAlertModel)
            .where(ThermalAlertModel.id == alerta_id, ThermalAlertModel.estado == EstadoAlerta.PENDIENTE.value)
            .values(
                estado=EstadoAlerta.RECONOCIDA.value,
                reconocida_en=timestamp,
                revisada=True,
                revisada_por=usuario_id,
            )
        )
        resultado = await self._session.execute(stmt)
        await self._session.flush()
        return resultado.rowcount == 1

    async def obtener_episodio_abierto(self, device_id: str) -> AlertaTermica | None:
        stmt = (
            select(ThermalAlertModel)
            .where(ThermalAlertModel.device_id == device_id, ThermalAlertModel.episodio_abierto == 1)
            .order_by(ThermalAlertModel.ultima_actualizacion.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_entity(model) if model else None

    async def marcar_notificada(self, alerta_id: UUID, momento: datetime) -> bool:
        """HU-53: marca atómica; False si otro envío ya la había marcado."""
        resultado = await self._session.execute(
            update(ThermalAlertModel)
            .where(ThermalAlertModel.id == alerta_id, ThermalAlertModel.notificada_en.is_(None))
            .values(notificada_en=momento)
        )
        await self._session.flush()
        return resultado.rowcount == 1

    async def registrar_intento_fallido(self, alerta_id: UUID) -> int:
        model = await self._session.get(ThermalAlertModel, alerta_id)
        if model is None:
            return 0
        model.notificacion_intentos = (model.notificacion_intentos or 0) + 1
        await self._session.flush()
        return model.notificacion_intentos

    async def listar_pendientes_de_notificar(
        self, desde: datetime, max_intentos: int
    ) -> list[AlertaTermica]:
        """Episodios críticos abiertos, recientes y aún sin aviso: lo que pudo
        quedar en la cola en memoria al reiniciarse el backend."""
        stmt = (
            select(ThermalAlertModel)
            .where(
                ThermalAlertModel.nivel_riesgo == NivelRiesgo.EXCURSION_CRITICA.value,
                ThermalAlertModel.episodio_abierto == 1,
                ThermalAlertModel.notificada_en.is_(None),
                ThermalAlertModel.notificacion_intentos < max_intentos,
                ThermalAlertModel.ultima_actualizacion >= desde,
            )
            .order_by(ThermalAlertModel.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return [_to_entity(m) for m in result.scalars().all()]

    async def obtener_ultimo_cerrado(
        self, device_id: str, nivel_riesgo: NivelRiesgo
    ) -> AlertaTermica | None:
        stmt = (
            select(ThermalAlertModel)
            .where(
                ThermalAlertModel.device_id == device_id,
                ThermalAlertModel.nivel_riesgo == nivel_riesgo.value,
                ThermalAlertModel.episodio_abierto.is_(None),
                ThermalAlertModel.cerrada_en.is_not(None),
            )
            .order_by(ThermalAlertModel.cerrada_en.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_entity(model) if model else None

    @staticmethod
    def _filtros_periodo(device_id: str | None, desde: datetime | None, hasta: datetime | None) -> list:
        """Filtros del periodo del reporte (HU-38/HU-51).

        El periodo se ancla en el instante de CAPTURA de la lectura que abrió
        el episodio (`thermal_readings.timestamp`), igual que el resto del
        reporte. Filtrar por `created_at` de la alerta (hora de inserción en
        la BD) desalinea el conteo cuando la ingesta llega con retraso
        (buffer offline del ESP32, colas MQTT) y deja episodios fuera del
        periodo al que pertenecen. Requiere el JOIN de `_con_lectura_inicial`.
        """
        filtros = []
        if device_id:
            filtros.append(ThermalAlertModel.device_id == device_id)
        if desde is not None:
            filtros.append(ThermalReadingModel.timestamp >= desde)
        if hasta is not None:
            filtros.append(ThermalReadingModel.timestamp <= hasta)
        return filtros

    @staticmethod
    def _con_lectura_inicial(stmt):
        return stmt.join(ThermalReadingModel, ThermalReadingModel.id == ThermalAlertModel.lectura_inicial_id)

    async def contar_por_nivel(
        self, device_id: str | None, desde: datetime | None, hasta: datetime | None
    ) -> dict[str, int]:
        stmt = (
            self._con_lectura_inicial(select(ThermalAlertModel.nivel_riesgo, func.count(ThermalAlertModel.id)))
            .where(*self._filtros_periodo(device_id, desde, hasta))
            .group_by(ThermalAlertModel.nivel_riesgo)
        )
        return {str(nivel): int(cantidad) for nivel, cantidad in (await self._session.execute(stmt)).all()}

    async def listar_por_nivel(
        self,
        nivel_riesgo: str,
        device_id: str | None,
        desde: datetime | None,
        hasta: datetime | None,
        limite: int = 10_000,
    ) -> list[AlertaTermica]:
        stmt = (
            self._con_lectura_inicial(select(ThermalAlertModel))
            .where(ThermalAlertModel.nivel_riesgo == nivel_riesgo, *self._filtros_periodo(device_id, desde, hasta))
            .order_by(ThermalReadingModel.timestamp.desc())
            .limit(limite)
        )
        return [_to_entity(m) for m in (await self._session.execute(stmt)).scalars().all()]


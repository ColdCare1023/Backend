from datetime import datetime

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.version_modelo_ia import VersionModeloIA
from src.domain.repositories.i_modelo_ia_repository import IModeloIARepository
from src.infrastructure.database.models import ModeloIAVersionModel, TraceabilityRecordModel

# HU-46 esc. 4: cada activación deja este evento encadenado (gestionar_modelo_ia)
# en la cadena del sistema (no tiene dispositivo; registrar_hash_encadenado).
EVENTO_VERSION_ACTIVADA = "MODELO_IA_VERSION_ACTIVADA"
# P-12: cada desactivación deja este evento; desde él no hay versión activa.
EVENTO_VERSION_DESACTIVADA = "MODELO_IA_VERSION_DESACTIVADA"
CADENA_SISTEMA = "SISTEMA"
# Clave del candado advisory que serializa activar/desactivar (P-12).
CANDADO_CAMBIO_DE_VERSION = "ai_model_versions"


def _to_entity(model: ModeloIAVersionModel) -> VersionModeloIA:
    return VersionModeloIA(
        id=model.id,
        version=model.version,
        model_hash=model.model_hash,
        feature_schema_version=model.feature_schema_version,
        dataset_version=model.dataset_version,
        scikit_learn_version=model.scikit_learn_version,
        python_version=model.python_version,
        trained_at=model.trained_at,
        aprobado_por=model.aprobado_por,
        aprobado_en=model.aprobado_en,
        activa=model.activa,
        activada_en=model.activada_en,
        desactivada_en=model.desactivada_en,
        horizonte_min=model.horizonte_min,
        metricas=model.metricas,
        particiones=model.particiones,
    )


class SQLAlchemyModeloIARepository(IModeloIARepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def registrar(self, version: VersionModeloIA) -> VersionModeloIA:
        model = ModeloIAVersionModel(
            version=version.version,
            model_hash=version.model_hash,
            feature_schema_version=version.feature_schema_version,
            dataset_version=version.dataset_version,
            scikit_learn_version=version.scikit_learn_version,
            python_version=version.python_version,
            trained_at=version.trained_at,
            aprobado_por=version.aprobado_por,
            horizonte_min=version.horizonte_min,
            metricas=version.metricas,
            particiones=version.particiones,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_entity(model)

    async def obtener_por_version(self, version: str) -> VersionModeloIA | None:
        stmt = select(ModeloIAVersionModel).where(ModeloIAVersionModel.version == version)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_entity(model) if model else None

    async def obtener_activa(self) -> VersionModeloIA | None:
        stmt = select(ModeloIAVersionModel).where(ModeloIAVersionModel.activa.is_(True))
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_entity(model) if model else None

    async def bloquear_cambio_de_version(self) -> None:
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            # Mismo patrón que la cadena de trazabilidad y los administradores:
            # candado advisory de transacción (se libera solo al confirmar o
            # deshacer). Sin él, /activar y /desactivar leían la versión activa
            # y tomaban su hora sin esperarse, y la historia de HU-47 esc. 2
            # podía contradecir a la base. El orden es siempre este candado y
            # después el de la cadena SISTEMA (registrar_hash_encadenado).
            await self._session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:clave))"), {"clave": CANDADO_CAMBIO_DE_VERSION}
            )

    async def activar(self, version: str, cuando: datetime) -> VersionModeloIA:
        anterior = await self.obtener_activa()
        if anterior is not None and anterior.version != version:
            model_anterior = await self._session.get(ModeloIAVersionModel, anterior.id)
            model_anterior.activa = False
            model_anterior.desactivada_en = cuando

        stmt = select(ModeloIAVersionModel).where(ModeloIAVersionModel.version == version)
        result = await self._session.execute(stmt)
        model = result.scalar_one()
        # Reactivar la versión que ya está activa no reinicia su periodo: las
        # lecturas clasificadas desde su activación siguen cruzando con ella.
        if not (model.activa and model.activada_en is not None):
            model.activada_en = cuando
        model.activa = True
        # Una versión que vuelve a activarse abre un periodo nuevo; los
        # anteriores quedan en los eventos encadenados de activación.
        model.desactivada_en = None
        await self._session.flush()
        await self._session.refresh(model)
        return _to_entity(model)

    async def desactivar(self, version: str, cuando: datetime) -> VersionModeloIA | None:
        # Un único UPDATE condicional: en PostgreSQL la segunda transacción
        # concurrente espera el bloqueo de la fila y, al reevaluar la
        # condición, ya no la encuentra activa (0 filas → None → 409).
        resultado = await self._session.execute(
            update(ModeloIAVersionModel)
            .where(ModeloIAVersionModel.version == version, ModeloIAVersionModel.activa.is_(True))
            .values(activa=False, desactivada_en=cuando)
            .execution_options(synchronize_session=False)
        )
        if resultado.rowcount != 1:
            return None
        model = (
            await self._session.execute(
                select(ModeloIAVersionModel)
                .where(ModeloIAVersionModel.version == version)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        return _to_entity(model)

    async def listar(self) -> list[VersionModeloIA]:
        stmt = select(ModeloIAVersionModel).order_by(ModeloIAVersionModel.aprobado_en.desc())
        result = await self._session.execute(stmt)
        return [_to_entity(m) for m in result.scalars().all()]

    async def obtener_version_activa_en(self, momento: datetime) -> VersionModeloIA | None:
        """HU-47 esc. 2: versión activa en `momento`. La fuente es la historia
        de solo anexado de activaciones y desactivaciones (eventos
        MODELO_IA_VERSION_ACTIVADA / _DESACTIVADA de la cadena), que conserva
        todos los periodos aunque una versión se reactive; las columnas
        activada_en/desactivada_en solo guardan el último y se usan si no hay
        eventos (registros anteriores a la cadena)."""
        fila = (
            await self._session.execute(
                select(TraceabilityRecordModel.tipo_evento, TraceabilityRecordModel.payload)
                .where(
                    # chain_id está indexado: no se recorren los eslabones de lecturas.
                    TraceabilityRecordModel.chain_id == CADENA_SISTEMA,
                    TraceabilityRecordModel.tipo_evento.in_((EVENTO_VERSION_ACTIVADA, EVENTO_VERSION_DESACTIVADA)),
                    TraceabilityRecordModel.timestamp <= momento,
                )
                .order_by(TraceabilityRecordModel.timestamp.desc(), TraceabilityRecordModel.chain_seq.desc())
                .limit(1)
            )
        ).first()
        # P-12: tras una desactivación no hay versión activa hasta la próxima activación.
        if fila is not None and fila.tipo_evento == EVENTO_VERSION_DESACTIVADA:
            return None
        evento = fila.payload if fila is not None else None
        if isinstance(evento, dict) and evento.get("version_nueva"):
            version = await self.obtener_por_version(str(evento["version_nueva"]))
            if version is not None:
                return version
        stmt = select(ModeloIAVersionModel).where(
            ModeloIAVersionModel.activada_en.is_not(None),
            ModeloIAVersionModel.activada_en <= momento,
            (ModeloIAVersionModel.desactivada_en.is_(None))
            | (ModeloIAVersionModel.desactivada_en > momento),
        )
        # Defensa: aunque activar() no deja periodos solapados, la consulta de
        # auditoría nunca debe responder 500 por MultipleResultsFound.
        stmt = stmt.order_by(ModeloIAVersionModel.activada_en.desc()).limit(1)
        result = await self._session.execute(stmt)
        model = result.scalars().first()
        return _to_entity(model) if model else None

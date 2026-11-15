from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.version_modelo_ia import VersionModeloIA
from src.domain.repositories.i_modelo_ia_repository import IModeloIARepository
from src.infrastructure.database.models import ModeloIAVersionModel, TraceabilityRecordModel

# HU-46 esc. 4: cada activación deja este evento encadenado (gestionar_modelo_ia)
# en la cadena del sistema (no tiene dispositivo; registrar_hash_encadenado).
EVENTO_VERSION_ACTIVADA = "MODELO_IA_VERSION_ACTIVADA"
CADENA_SISTEMA = "SISTEMA"


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

    async def listar(self) -> list[VersionModeloIA]:
        stmt = select(ModeloIAVersionModel).order_by(ModeloIAVersionModel.aprobado_en.desc())
        result = await self._session.execute(stmt)
        return [_to_entity(m) for m in result.scalars().all()]

    async def obtener_version_activa_en(self, momento: datetime) -> VersionModeloIA | None:
        """HU-47 esc. 2: versión activa en `momento`. La fuente es la historia
        de solo anexado de activaciones (eventos MODELO_IA_VERSION_ACTIVADA de
        la cadena), que conserva todos los periodos aunque una versión se
        reactive; las columnas activada_en/desactivada_en solo guardan el
        último y se usan si no hay eventos (registros anteriores a la cadena)."""
        evento = (
            await self._session.execute(
                select(TraceabilityRecordModel.payload)
                .where(
                    # chain_id está indexado: no se recorren los eslabones de lecturas.
                    TraceabilityRecordModel.chain_id == CADENA_SISTEMA,
                    TraceabilityRecordModel.tipo_evento == EVENTO_VERSION_ACTIVADA,
                    TraceabilityRecordModel.timestamp <= momento,
                )
                .order_by(TraceabilityRecordModel.timestamp.desc(), TraceabilityRecordModel.chain_seq.desc())
                .limit(1)
            )
        ).scalars().first()
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

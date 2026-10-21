from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.application.use_cases.gestionar_firmware import (
    EjecutarDespliegueUseCase,
    EnviarOrdenOtaUseCase,
    OrdenOtaNoEnviadaError,
    PrepararFirmwareUseCase,
    ProgramarDespliegueUseCase,
)
from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.exceptions import DomainError, RecursoNoEncontradoError
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.firmware_repository import SQLAlchemyFirmwareRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.infrastructure.mqtt.ordenes_ota import PublicadorOrdenesOTA, obtener_publicador_ota
from src.interface.api.deps import DbSessionDep, SettingsDep, require_roles
from src.interface.api.schemas import (
    FirmwareDespliegueCreateRequest,
    FirmwareDespliegueResponse,
    FirmwareReleaseCreateRequest,
    FirmwareReleaseResponse,
)

router = APIRouter(prefix="/api/firmware", tags=["firmware-ota"])


def get_publicador_ota(settings: SettingsDep) -> PublicadorOrdenesOTA:
    return obtener_publicador_ota(settings)


PublicadorOtaDep = Annotated[PublicadorOrdenesOTA, Depends(get_publicador_ota)]


def _traducir(exc: Exception) -> HTTPException:
    if isinstance(exc, RecursoNoEncontradoError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post("/releases", response_model=FirmwareReleaseResponse, status_code=status.HTTP_201_CREATED)
async def preparar_release(
    body: FirmwareReleaseCreateRequest,
    session: DbSessionDep,
    _admin=Depends(require_roles(Rol.ADMINISTRADOR)),
) -> FirmwareReleaseResponse:
    use_case = PrepararFirmwareUseCase(
        SQLAlchemyFirmwareRepository(session), RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session))
    )
    try:
        release = await use_case.execute(
            body.version, body.hash_sha256, body.descripcion, body.url_descarga, body.tamano_bytes
        )
    except DomainError as exc:
        raise _traducir(exc) from exc
    await session.commit()
    return FirmwareReleaseResponse(**release)


@router.get("/releases", response_model=list[FirmwareReleaseResponse])
async def listar_releases(session: DbSessionDep, _admin=Depends(require_roles(Rol.ADMINISTRADOR))) -> list[FirmwareReleaseResponse]:
    releases = await SQLAlchemyFirmwareRepository(session).listar_releases()
    return [FirmwareReleaseResponse(**r) for r in releases]


@router.post("/despliegues", response_model=FirmwareDespliegueResponse, status_code=status.HTTP_201_CREATED)
async def programar_despliegue(
    body: FirmwareDespliegueCreateRequest,
    session: DbSessionDep,
    _admin=Depends(require_roles(Rol.ADMINISTRADOR)),
) -> FirmwareDespliegueResponse:
    """HU-46 Escenario 2: valida no-downgrade antes de aceptar la programación."""
    use_case = ProgramarDespliegueUseCase(
        SQLAlchemyDeviceRepository(session), SQLAlchemyFirmwareRepository(session)
    )
    try:
        despliegue = await use_case.execute(body.device_id, body.version_objetivo, body.programado_para)
    except (DomainError, RecursoNoEncontradoError) as exc:
        raise _traducir(exc) from exc
    await session.commit()
    return FirmwareDespliegueResponse(**despliegue)


@router.post("/despliegues/{despliegue_id}/ejecutar", response_model=FirmwareDespliegueResponse)
async def ejecutar_despliegue(
    despliegue_id: UUID,
    session: DbSessionDep,
    settings: SettingsDep,
    publicador: PublicadorOtaDep,
    _admin=Depends(require_roles(Rol.ADMINISTRADOR)),
) -> FirmwareDespliegueResponse:
    """HU-46: ejecuta el despliegue programado.

    IOT-004: si la release tiene URL de descarga y tamaño, se publica una
    orden OTA real para el nodo y el despliegue queda `enviado` hasta que el
    propio nodo confirme (o no) la instalación. Sin ellos solo existe el flujo
    simulado de desarrollo, que no se sirve en producción (BE-016)."""
    firmware_repository = SQLAlchemyFirmwareRepository(session)
    despliegue = await firmware_repository.obtener_despliegue(despliegue_id)
    release = (
        await firmware_repository.obtener_release(despliegue["version_objetivo"]) if despliegue else None
    )
    if release is not None and release.get("url_descarga"):
        if not publicador.configurado:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="MQTT no está configurado: no se puede enviar la orden OTA.",
            )
        caso_real = EnviarOrdenOtaUseCase(
            SQLAlchemyDeviceRepository(session),
            firmware_repository,
            RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session)),
            publicador.publicar,
        )
        try:
            enviado = await caso_real.execute(despliegue_id)
        except OrdenOtaNoEnviadaError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except (DomainError, RecursoNoEncontradoError) as exc:
            raise _traducir(exc) from exc
        await session.commit()
        return FirmwareDespliegueResponse(**enviado)

    if settings.environment == "production":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="La release no tiene URL de descarga: la ejecución simulada no está disponible en producción.",
        )
    use_case = EjecutarDespliegueUseCase(
        SQLAlchemyDeviceRepository(session),
        SQLAlchemyFirmwareRepository(session),
        RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session)),
    )
    try:
        despliegue = await use_case.execute(despliegue_id)
    except (DomainError, RecursoNoEncontradoError) as exc:
        raise _traducir(exc) from exc
    await session.commit()
    return FirmwareDespliegueResponse(**despliegue)

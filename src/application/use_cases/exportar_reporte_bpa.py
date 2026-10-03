from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from src.domain.entities.alerta_termica import AlertaTermica
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.entities.registro_trazabilidad import RegistroTrazabilidad
from src.domain.repositories.i_alerta_repository import IAlertaRepository
from src.domain.repositories.i_lectura_repository import ILecturaRepository
from src.domain.repositories.i_reporte_repository import IReporteRepository
from src.domain.repositories.i_trazabilidad_repository import ITrazabilidadRepository

# Tope de registros por colección en un reporte.
#
# Existe por memoria: 10.000 lecturas + 10.000 alertas + 10.000 registros de
# trazabilidad convertidos a objetos de respuesta ocupan ~43 MB, y serializarlos
# a JSON llega a un pico de ~81 MB. La instancia de Railway tiene 512 MB
# compartidos con el modelo Random Forest, el pool de conexiones y las colas SSE.
#
# La consecuencia hay que decirla: a la cadencia de muestreo del firmware
# (30 s → 2.880 lecturas/día) este tope cubre unos 3,5 días. Un reporte mensual
# NO cabe entero, y por eso el reporte declara explícitamente que se truncó.
# Un documento de cumplimiento que omite datos en silencio no es evidencia.
LIMITE_REGISTROS_REPORTE = 10_000


@dataclass(frozen=True, slots=True)
class ReporteBPA:
    device_id: str | None
    fecha_desde: datetime
    fecha_hasta: datetime
    lecturas: list[LecturaTermica]
    alertas: list[AlertaTermica]
    registros_trazabilidad: list[RegistroTrazabilidad]
    # Cada bandera indica que esa colección alcanzó el tope y quedó recortada:
    # hay más registros en el periodo de los que el reporte muestra.
    lecturas_truncadas: bool = False
    alertas_truncadas: bool = False
    trazabilidad_truncada: bool = False
    # Tope efectivamente aplicado. Lo declara el caso de uso y no el router:
    # es quien lo conoce, y así el valor informado nunca puede desviarse del
    # que se usó de verdad.
    limite_aplicado: int = LIMITE_REGISTROS_REPORTE

    @property
    def truncado(self) -> bool:
        return self.lecturas_truncadas or self.alertas_truncadas or self.trazabilidad_truncada


async def listar_detectando_truncamiento(consulta, **kwargs) -> tuple[list, bool]:
    """Ejecuta `consulta` pidiendo un registro de más que el tope.

    Si vuelve ese registro extra es que el periodo contiene más datos de los que
    caben; se descarta y se devuelve la bandera. Pedir uno de más es mucho más
    barato que un `COUNT(*)` sobre la serie temporal completa, y es todo lo que
    hace falta para poder DECLARAR el recorte.
    """
    limite = kwargs.pop("limite", LIMITE_REGISTROS_REPORTE)
    registros = await consulta(limite=limite + 1, **kwargs)
    if len(registros) > limite:
        return registros[:limite], True
    return registros, False


class ExportarReporteBPAUseCase:
    """RF-13: reporte exportable con soporte documental de Buenas Prácticas de
    Almacenamiento (historial térmico, alertas y trazabilidad del periodo)."""

    def __init__(
        self,
        lectura_repository: ILecturaRepository,
        alerta_repository: IAlertaRepository,
        trazabilidad_repository: ITrazabilidadRepository,
        reporte_repository: IReporteRepository,
    ) -> None:
        self._lectura_repository = lectura_repository
        self._alerta_repository = alerta_repository
        self._trazabilidad_repository = trazabilidad_repository
        self._reporte_repository = reporte_repository

    async def execute(
        self,
        usuario_id: UUID,
        fecha_desde: datetime,
        fecha_hasta: datetime,
        device_id: str | None = None,
    ) -> ReporteBPA:
        # RF-13: las tres colecciones se ciñen al mismo periodo. Antes solo las
        # lecturas se acotaban, así que el reporte de un mes se acompañaba de
        # las alertas y la trazabilidad de todo el histórico.
        lecturas, lecturas_truncadas = await listar_detectando_truncamiento(
            self._lectura_repository.listar,
            device_id=device_id, desde=fecha_desde, hasta=fecha_hasta,
        )
        alertas, alertas_truncadas = await listar_detectando_truncamiento(
            self._alerta_repository.listar,
            device_id=device_id, desde=fecha_desde, hasta=fecha_hasta,
        )
        trazabilidad, trazabilidad_truncada = await listar_detectando_truncamiento(
            self._trazabilidad_repository.listar,
            device_id=device_id, desde=fecha_desde, hasta=fecha_hasta,
        )

        await self._reporte_repository.registrar_exportacion(
            usuario_id=usuario_id,
            tipo_reporte="BPA",
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )

        return ReporteBPA(
            device_id=device_id,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            lecturas=lecturas,
            alertas=alertas,
            registros_trazabilidad=trazabilidad,
            lecturas_truncadas=lecturas_truncadas,
            alertas_truncadas=alertas_truncadas,
            trazabilidad_truncada=trazabilidad_truncada,
            limite_aplicado=LIMITE_REGISTROS_REPORTE,
        )

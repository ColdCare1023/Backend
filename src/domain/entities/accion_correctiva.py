from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(slots=True)
class AccionCorrectiva:
    alert_id: UUID
    usuario_id: UUID
    descripcion: str
    id: UUID | None = None
    created_at: datetime | None = None
    # HU-28: cuando esta acción CORRIGE una justificación previa, referencia
    # a esa acción anterior en vez de sobrescribirla — la anterior nunca se
    # modifica ni se borra, así se reconstruye el orden real de decisiones.
    corrige_accion_id: UUID | None = None

from uuid import UUID

from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.entities.checklist_bpa import ChecklistBPA
from src.domain.repositories.i_checklist_repository import IChecklistRepository


class RegistrarChecklistBPAUseCase:
    """HU-37: persiste la verificación diaria de Buenas Prácticas de
    Almacenamiento y la ancla a la cadena de trazabilidad SHA-256 (RF-14).

    El eslabón de trazabilidad se emite en CADA guardado, incluso cuando el
    checklist del día ya existía y solo se corrigió un ítem: la fila se
    actualiza, pero la declaración anterior queda inmutable en la cadena. Eso
    es lo que permite demostrar ante una inspección qué se declaró, cuándo y
    quién lo modificó después.
    """

    def __init__(
        self,
        checklist_repository: IChecklistRepository,
        registrar_hash: RegistrarHashEncadenadoUseCase,
    ) -> None:
        self._checklist_repository = checklist_repository
        self._registrar_hash = registrar_hash

    async def execute(self, checklist: ChecklistBPA) -> ChecklistBPA:
        checklist.validar()

        previo = await self._checklist_repository.obtener_por_usuario_y_fecha(
            checklist.usuario_id, checklist.fecha
        )
        guardado = await self._checklist_repository.guardar(checklist)

        await self._registrar_hash.execute(
            tipo_evento="CHECKLIST_BPA",
            payload={
                "checklist_id": str(guardado.id),
                "usuario_id": str(guardado.usuario_id),
                "fecha": guardado.fecha,
                "items": guardado.items(),
                "total_conformes": guardado.total_conformes(),
                "conforme": guardado.es_conforme(),
                "observaciones": guardado.observaciones,
                # Distingue el alta del día de una corrección posterior: ambas
                # dejan eslabón, pero solo la segunda invalida una declaración
                # previa ya firmada en la cadena.
                "correccion_de_registro_previo": previo is not None,
            },
            usuario_id=guardado.usuario_id,
        )
        return guardado


class ConsultarChecklistBPAUseCase:
    def __init__(self, checklist_repository: IChecklistRepository) -> None:
        self._checklist_repository = checklist_repository

    async def obtener_del_dia(self, usuario_id: UUID, fecha: str) -> ChecklistBPA | None:
        return await self._checklist_repository.obtener_por_usuario_y_fecha(usuario_id, fecha)

    async def obtener_ultimo(self, usuario_id: UUID) -> ChecklistBPA | None:
        return await self._checklist_repository.obtener_ultimo_por_usuario(usuario_id)

    async def listar_historial(
        self, usuario_id: UUID, limite: int = 50, offset: int = 0
    ) -> list[ChecklistBPA]:
        return await self._checklist_repository.listar_por_usuario(usuario_id, limite, offset)

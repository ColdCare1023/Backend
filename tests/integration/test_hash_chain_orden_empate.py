"""Verificación global con eslabones que comparten `created_at`.

Con un reloj de resolución gruesa (Python 3.12 en Windows: ~15,6 ms) varios
eslabones consecutivos de una cadena reciben el mismo `created_at`. El
recorrido global los desempataba por `id` (UUID aleatorio) y podía invertir su
orden de `chain_seq`: la verificación informaba un enlace roto que no existía.
Aquí el reloj se congela para reproducir el empate en cualquier plataforma.
"""

from datetime import datetime, timezone

from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.application.use_cases.verificar_integridad_registro import VerificarIntegridadRegistroUseCase
from src.infrastructure.database import models
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository

INSTANTE = datetime(2026, 1, 1, tzinfo=timezone.utc)
N_ESLABONES = 12


class _RelojCongelado(datetime):
    @classmethod
    def now(cls, tz=None):
        return INSTANTE


async def test_eslabones_con_el_mismo_created_at_se_verifican_en_orden_de_chain_seq(db_session, monkeypatch):
    monkeypatch.setattr(models, "datetime", _RelojCongelado)
    repo = SQLAlchemyTrazabilidadRepository(db_session)
    registrar = RegistrarHashEncadenadoUseCase(repo)
    for indice in range(N_ESLABONES):
        await registrar.execute("EVENTO", {"i": indice}, device_id=f"D-EMPATE-{indice % 2}")
    await db_session.commit()

    registros = await repo.listar_todos_ordenados()
    assert {r.chain_id for r in registros} == {"D-EMPATE-0", "D-EMPATE-1"}

    resultado = await VerificarIntegridadRegistroUseCase(repo).execute()

    assert resultado.total_registros == N_ESLABONES
    assert resultado.integra is True
    assert resultado.primer_registro_inconsistente is None

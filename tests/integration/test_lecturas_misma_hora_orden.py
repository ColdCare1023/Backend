"""Lecturas con la misma hora de captura: el orden «más reciente primero» es
determinista.

El firmware puede repetir `timestamp` (sin NTP, o varias lecturas en el mismo
segundo, como hace `infra-local/scripts/nodo_virtual.py`). Solo con
`timestamp DESC` la base devolvía cualquiera de las empatadas, y la «última
temperatura» del dashboard mostraba una lectura anterior (4.3 °C en vez de la
excursión de 9.1 °C enviada después).
"""

from datetime import datetime, timedelta, timezone

from src.domain.entities.lectura_termica import LecturaTermica
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository

DEVICE_ID = "FARM-ORDEN-01"
_HORA = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(minutes=5)


def _lectura(seq_no: int, temperatura: float, llegada: datetime) -> LecturaTermica:
    return LecturaTermica(
        device_id=DEVICE_ID,
        timestamp=_HORA,
        temperatura_ambiental=21.0,
        humedad_ambiental=55.0,
        temperatura_interna=temperatura,
        apertura_refrigerador=False,
        estado_conectividad="online",
        boot_id=884294,
        seq_no=seq_no,
        received_at=llegada,
    )


async def test_misma_hora_gana_la_ultima_secuencia_del_arranque(db_session_factory):
    # Se insertan desordenadas para que el orden físico de la tabla no ayude.
    async with db_session_factory() as session:
        repo = SQLAlchemyLecturaRepository(session)
        for seq_no, temperatura in ((2, 4.30), (5, 9.12), (1, 4.25), (4, 4.40), (3, 4.35)):
            await repo.agregar(_lectura(seq_no, temperatura, _HORA + timedelta(milliseconds=seq_no * 50)))
        await session.commit()

    async with db_session_factory() as session:
        repo = SQLAlchemyLecturaRepository(session)
        ultima = await repo.listar(device_id=DEVICE_ID, limite=1)
        recientes = await repo.listar_recientes_por_device(DEVICE_ID, limite=5)

    assert [lectura.seq_no for lectura in ultima] == [5]
    assert ultima[0].temperatura_interna == 9.12
    assert [lectura.seq_no for lectura in recientes] == [5, 4, 3, 2, 1]

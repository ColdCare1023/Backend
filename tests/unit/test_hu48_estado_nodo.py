"""HU-48: interpretación del `estado_nodo` y de `buffer_saturado`, episodios de
buffer agotado y vista del buffer LittleFS (sin base de datos)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from src.application.use_cases.registrar_estado_nodo import (
    CAPACIDAD_BUFFER_POR_DEFECTO,
    RegistrarEpisodioBufferUseCase,
    RegistrarEstadoNodoUseCase,
    construir_vista_buffer,
    interpretar_buffer_saturado,
    interpretar_estado_nodo,
)
from src.domain.repositories.i_estado_nodo_repository import (
    EpisodioBufferSaturado,
    EstadoNodo,
    IEstadoNodoRepository,
)

EJEMPLOS = Path(__file__).resolve().parents[1] / "fixtures" / "contrato_v1"
T0 = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)

# Campos que publica el firmware en `publicarEstadoNodo` (IoT src/main.cpp).
DETALLE_FIRMWARE = {
    "pendientes": 240, "ocupacion_pct": 1, "capacidad": 30000, "fs_total_bytes": 1441792,
    "fs_libres_bytes": 1200000, "perdidas_total": 3, "cuarentena": 0, "ultimo_seq_no": 1500,
    "rssi_dbm": -61, "heap_libre": 150000, "heap_minimo": 120000, "intervalo_s": 30, "pubacks_sin_ack": 0,
    "acks_huella_ajena": 0, "eventos_descartados": 0, "reloj_sntp": True, "saltos_reloj_rechazados": 0,
}


class _RepoMemoria(IEstadoNodoRepository):
    def __init__(self) -> None:
        self.estados: dict[str, EstadoNodo] = {}
        self.episodios: dict = {}

    async def obtener_estado(self, device_id):
        return self.estados.get(device_id)

    async def guardar_estado(self, estado):
        self.estados[estado.device_id] = estado
        return estado

    async def episodio_por_inicio(self, device_id, desde_boot_id, desde_seq_no):
        return next(
            (e for e in self.episodios.values()
             if (e.device_id, e.desde_boot_id, e.desde_seq_no) == (device_id, desde_boot_id, desde_seq_no)),
            None,
        )

    async def episodios_abiertos(self, device_id):
        return [e for e in self.episodios.values() if e.device_id == device_id and e.abierto]

    async def agregar_episodio(self, episodio):
        self.episodios[episodio.id] = episodio
        return episodio

    async def actualizar_episodio(self, episodio):
        self.episodios[episodio.id] = episodio
        return episodio

    async def listar_episodios(self, device_id, limite=10, desde=None, hasta=None):
        return sorted(
            (e for e in self.episodios.values() if e.device_id == device_id),
            key=lambda e: e.registrado_en, reverse=True,
        )[:limite]


def _detalle_buffer_saturado(**cambios) -> dict:
    detalle = json.loads((EJEMPLOS / "evento_buffer_saturado.json").read_text(encoding="utf-8"))["detail"]
    return {**detalle, **cambios}


def test_estado_nodo_del_firmware_se_interpreta():
    latido = interpretar_estado_nodo(DETALLE_FIRMWARE)
    assert (latido.pendientes, latido.capacidad, latido.ocupacion_pct) == (240, 30000, 1)
    assert (latido.perdidas_total, latido.cuarentena) == (3, 0)
    assert latido.pendientes_desde is None and latido.pendientes_hasta is None


def test_estado_nodo_ignora_valores_fuera_de_tipo_sin_rechazar_el_latido():
    latido = interpretar_estado_nodo({"pendientes": True, "capacidad": "30000", "ocupacion_pct": 140,
                                      "perdidas_total": -1, "pendientes_desde": "ayer"})
    assert latido == interpretar_estado_nodo({})


def test_estado_nodo_admite_el_rango_de_los_pendientes():
    latido = interpretar_estado_nodo({"pendientes": 2, "pendientes_desde": "2026-11-14T09:00:00Z",
                                      "pendientes_hasta": "2026-11-14T09:00:30Z"})
    assert latido.pendientes_desde == datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)
    assert latido.pendientes_hasta == datetime(2026, 11, 14, 9, 0, 30, tzinfo=timezone.utc)


def test_buffer_saturado_del_contrato_se_interpreta():
    aviso = interpretar_buffer_saturado(_detalle_buffer_saturado())
    assert (aviso.perdidas, aviso.perdidas_total, aviso.abierto, aviso.causa) == (3, 3, True, "buffer_lleno")
    assert (aviso.desde_boot_id, aviso.desde_seq_no, aviso.hasta_seq_no) == (42, 1300, 1302)
    assert aviso.desde == datetime(2026, 9, 25, 16, 20, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_avisos_del_mismo_episodio_actualizan_una_sola_brecha():
    repo = _RepoMemoria()
    caso = RegistrarEpisodioBufferUseCase(repo)
    await caso.execute("N1", _detalle_buffer_saturado(), recibido_en=T0)
    await caso.execute(
        "N1",
        _detalle_buffer_saturado(perdidas_episodio=20, hasta_seq_no=1319, hasta="2026-09-25T16:30:00Z"),
        recibido_en=T0 + timedelta(minutes=10),
    )
    cerrado = await caso.execute(
        "N1",
        _detalle_buffer_saturado(perdidas_episodio=25, episodio_abierto=False, hasta_seq_no=1324,
                                 hasta="2026-09-25T16:33:00Z"),
        recibido_en=T0 + timedelta(minutes=13),
    )

    assert len(repo.episodios) == 1
    assert (cerrado.perdidas, cerrado.abierto, cerrado.hasta_seq_no) == (25, False, 1324)
    assert cerrado.desde == datetime(2026, 9, 25, 16, 20, tzinfo=timezone.utc)
    assert cerrado.hasta == datetime(2026, 9, 25, 16, 33, tzinfo=timezone.utc)
    assert cerrado.registrado_en == T0 and cerrado.cerrado_en == T0 + timedelta(minutes=13)


@pytest.mark.asyncio
async def test_un_episodio_nuevo_cierra_el_anterior_que_quedo_abierto():
    """Si el nodo se reinició sin enviar el cierre, el episodio no queda
    abierto para siempre."""
    repo = _RepoMemoria()
    caso = RegistrarEpisodioBufferUseCase(repo)
    primero = await caso.execute("N1", _detalle_buffer_saturado(), recibido_en=T0)
    await caso.execute("N1", _detalle_buffer_saturado(desde_boot_id=43, desde_seq_no=10), recibido_en=T0 + timedelta(hours=1))

    assert repo.episodios[primero.id].abierto is False
    assert len(await repo.episodios_abiertos("N1")) == 1


@pytest.mark.asyncio
async def test_estado_nodo_conserva_la_ultima_sincronizacion():
    repo = _RepoMemoria()
    caso = RegistrarEstadoNodoUseCase(repo)
    await caso.execute("N1", {"pendientes": 0}, recibido_en=T0)
    estado = await caso.execute(
        "N1", {"pendientes": 12, "pendientes_desde": "2026-11-14T10:01:00Z"}, recibido_en=T0 + timedelta(minutes=5)
    )
    assert estado.ultima_sincronizacion_en == T0
    assert estado.pendientes_desde == datetime(2026, 11, 14, 10, 1, tzinfo=timezone.utc)
    vacio = await caso.execute("N1", {"pendientes": 0, "pendientes_desde": "2026-11-14T10:01:00Z"},
                               recibido_en=T0 + timedelta(minutes=10))
    assert vacio.ultima_sincronizacion_en == T0 + timedelta(minutes=10)
    assert vacio.pendientes_desde is None


def test_sin_estado_ni_brechas_no_se_inventa_un_buffer():
    assert construir_vista_buffer(None, [], None) is None


def test_rango_de_pendientes_se_estima_si_el_firmware_no_lo_informa():
    """HU-48 esc. 1: número de registros retenidos y rango que cubren."""
    estado = EstadoNodo(device_id="N1", recibido_en=T0, ocurrido_en=T0 - timedelta(seconds=2), pendientes=240,
                        capacidad=None, perdidas_total=None)
    ultima = T0 - timedelta(hours=2)
    vista = construir_vista_buffer(estado, [], ultima)

    assert (vista.pendientes, vista.limite) == (240, CAPACIDAD_BUFFER_POR_DEFECTO)
    assert (vista.desde, vista.hasta, vista.rango_estimado) == (ultima, T0 - timedelta(seconds=2), True)
    assert vista.perdidas == 0


def test_rango_informado_por_el_firmware_no_es_estimado():
    desde, hasta = T0 - timedelta(hours=1), T0 - timedelta(seconds=30)
    estado = EstadoNodo(device_id="N1", recibido_en=T0, pendientes=120, pendientes_desde=desde, pendientes_hasta=hasta)
    vista = construir_vista_buffer(estado, [], None)
    assert (vista.desde, vista.hasta, vista.rango_estimado) == (desde, hasta, False)


def test_sincronizado_no_muestra_rango_y_las_perdidas_salen_del_contador_del_nodo():
    """HU-48 esc. 3: con 0 pendientes el indicador desaparece."""
    episodio = EpisodioBufferSaturado(
        id=uuid4(), device_id="N1", causa="buffer_lleno", perdidas=3, abierto=False,
        registrado_en=T0, actualizado_en=T0,
    )
    estado = EstadoNodo(device_id="N1", recibido_en=T0, pendientes=0, capacidad=30000, perdidas_total=7,
                        ultima_sincronizacion_en=T0)
    vista = construir_vista_buffer(estado, [episodio], T0)
    assert (vista.pendientes, vista.desde, vista.hasta, vista.perdidas) == (0, None, None, 7)
    assert vista.ultima_sincronizacion_en == T0 and vista.brechas == [episodio]

"""HU-38 y HU-51: el reporte PDF cubre el periodo completo.

HU-38 esc. 3: resumen y excursiones con agregados SQL sobre TODO el periodo,
con independencia del tope de filas del detalle; ninguna excursión crítica se
omite. Esc. 2: referencia reproducible completa (64 hex) y cita de la última
verificación HU-37 del periodo. HU-51 esc. 4: ubicación vigente DURANTE el
periodo reconstruida del historial, referencia al historial e instalador."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.application.use_cases import exportar_reporte_bpa
from src.application.use_cases.exportar_reporte_bpa_pdf import ExportarReporteBPAPDFUseCase
from src.application.use_cases.verificar_integridad_registro import VerificarIntegridadRegistroUseCase
from src.infrastructure.database.models import ThermalAlertModel, ThermalReadingModel
from src.infrastructure.database.repositories.accion_correctiva_repository import (
    SQLAlchemyAccionCorrectivaRepository,
)
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.checklist_repository import SQLAlchemyChecklistRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.reporte_repository import SQLAlchemyReporteRepository
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.pdf.generador_pdf import GeneradorReporteBPAPDF
from src.interface.api.mappers import nombre_visible
from tests.conftest import auth_header

DEVICE = "FARM-HU38-01"
AHORA = datetime.now(tz=timezone.utc).replace(microsecond=0)


class _GeneradorEspia:
    """Captura lo que el caso de uso entrega al maquetador."""

    def __init__(self) -> None:
        self.datos: dict = {}

    def generar(self, **datos) -> bytes:
        self.datos = datos
        return b"%PDF-1.4"


def _textos(flowables) -> str:
    salida = []
    for f in flowables:
        for fila in getattr(f, "_cellvalues", []):
            salida.extend(getattr(c, "text", str(c)) for c in fila)
        if hasattr(f, "text"):
            salida.append(f.text)
    return " ".join(salida)


def _ingestar(client, token, temperatura: float, minutos_atras: int) -> None:
    respuesta = client.post(
        "/api/lecturas",
        json={
            "device_id": DEVICE,
            "timestamp": (AHORA - timedelta(minutes=minutos_atras)).isoformat(),
            "temperatura_interna": temperatura,
            "temperatura_ambiental": 21.0,
            "humedad_ambiental": 50.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        },
        headers=auth_header(token),
    )
    assert respuesta.status_code == 201, respuesta.text


async def _reporte(fabrica, desde: datetime, hasta: datetime) -> dict:
    espia = _GeneradorEspia()
    async with fabrica() as session:
        trazabilidad = SQLAlchemyTrazabilidadRepository(session)
        usuarios = SQLAlchemyUsuarioRepository(session)

        async def nombre(usuario_id):
            return nombre_visible(await usuarios.obtener_por_id(usuario_id))

        await ExportarReporteBPAPDFUseCase(
            SQLAlchemyLecturaRepository(session),
            SQLAlchemyAlertaRepository(session),
            trazabilidad,
            SQLAlchemyReporteRepository(session),
            SQLAlchemyChecklistRepository(session),
            VerificarIntegridadRegistroUseCase(trazabilidad),
            generador=espia,
            accion_repository=SQLAlchemyAccionCorrectivaRepository(session),
            device_repository=SQLAlchemyDeviceRepository(session),
            nombre_de_usuario=nombre,
        ).execute(
            usuario_id=(await usuarios.obtener_por_email("farmaceutico@farmacia.example.org")).id,
            usuario_nombre="QF",
            fecha_desde=desde,
            fecha_hasta=hasta,
            device_id=DEVICE,
        )
        await session.rollback()
    return espia.datos


# ── HU-38 esc. 3: agregados del periodo completo ──────────────────────────


async def test_el_resumen_cubre_todo_el_periodo_aunque_el_detalle_se_recorte(
    client, token_tecnico, token_farmaceutico, db_session_factory, monkeypatch
):
    temperaturas = [5.0, 6.0, 9.5, 10.0, 4.0, 1.0]
    for i, temperatura in enumerate(temperaturas):
        _ingestar(client, token_tecnico, temperatura, minutos_atras=60 - i * 5)
    monkeypatch.setattr(exportar_reporte_bpa, "LIMITE_REGISTROS_REPORTE", 3)

    datos = await _reporte(db_session_factory, AHORA - timedelta(hours=2), AHORA + timedelta(minutes=1))

    assert len(datos["lecturas"]) == 3 and datos["truncamiento"]["lecturas_truncadas"] is True
    est = datos["estadisticas"]
    assert est["fuente"] == "periodo_completo"
    assert est["total_lecturas"] == 6
    assert est["lecturas_con_temperatura"] == 6
    assert est["temp_minima"] == 1.0 and est["temp_maxima"] == 10.0
    assert est["temp_promedio"] == pytest.approx(sum(temperaturas) / 6)
    assert est["porcentaje_en_rango"] == pytest.approx(50.0)
    assert est["alertas_criticas"] == 3  # lecturas en excursión crítica (riesgo efectivo)
    assert sum(est["por_nivel"].values()) == 6
    async with db_session_factory() as session:
        criticas = (
            await session.execute(
                select(ThermalAlertModel).where(
                    ThermalAlertModel.device_id == DEVICE, ThermalAlertModel.nivel_riesgo == "excursion_critica"
                )
            )
        ).scalars().all()
    assert est["episodios_criticos"] == len(criticas) >= 1


async def test_episodios_se_cuentan_por_instante_de_captura_no_de_insercion(
    client, token_tecnico, token_farmaceutico, db_session_factory
):
    """Ingesta tardía (buffer offline, cola MQTT): la alerta se inserta AHORA
    pero el episodio ocurrió hace una hora. Cuenta en el periodo de la captura
    y no en el de la inserción."""
    for i, temperatura in enumerate([5.0, 9.5, 10.0, 5.0]):
        _ingestar(client, token_tecnico, temperatura, minutos_atras=60 - i * 5)

    periodo_de_captura = await _reporte(db_session_factory, AHORA - timedelta(hours=2), AHORA - timedelta(minutes=30))
    assert periodo_de_captura["estadisticas"]["episodios_criticos"] >= 1

    periodo_de_insercion = await _reporte(db_session_factory, AHORA - timedelta(minutes=20), AHORA + timedelta(hours=1))
    assert periodo_de_insercion["estadisticas"]["episodios_criticos"] == 0


async def test_ninguna_excursion_critica_se_omite_si_las_alertas_se_recortan(
    client, token_tecnico, token_farmaceutico, db_session_factory, monkeypatch
):
    _ingestar(client, token_tecnico, 5.0, minutos_atras=90)
    async with db_session_factory() as session:
        lectura = (await session.execute(select(ThermalReadingModel).limit(1))).scalar_one()
        for i, nivel in enumerate(["excursion_critica"] * 3 + ["riesgo_preventivo"] * 3):
            alerta = ThermalAlertModel(
                reading_id=lectura.id, device_id=DEVICE, nivel_riesgo=nivel, mensaje=f"{nivel}-{i}",
                lectura_inicial_id=lectura.id, lectura_mas_reciente_id=lectura.id,
                created_at=AHORA - timedelta(minutes=80 - i * 10),
            )
            session.add(alerta)
            await session.flush()
            alerta.episodio_abierto = None  # episodio cerrado: admite varios del mismo nivel
            await session.flush()
        await session.commit()
    monkeypatch.setattr(exportar_reporte_bpa, "LIMITE_REGISTROS_REPORTE", 2)

    datos = await _reporte(db_session_factory, AHORA - timedelta(hours=2), AHORA + timedelta(minutes=1))

    assert datos["truncamiento"]["alertas_truncadas"] is True
    criticas = [a for a in datos["alertas"] if a["nivel_riesgo"] == "excursion_critica"]
    assert sorted(a["mensaje"] for a in criticas) == [f"excursion_critica-{i}" for i in range(3)]
    assert datos["estadisticas"]["episodios_criticos"] == 3
    assert datos["estadisticas"]["episodios_por_nivel"]["riesgo_preventivo"] == 3


# ── HU-38 esc. 2: digest completo y última verificación HU-37 ─────────────


async def test_el_reporte_lleva_el_hash_final_completo_y_cita_la_verificacion_del_periodo(
    client, token_tecnico, token_farmaceutico, db_session_factory
):
    _ingestar(client, token_tecnico, 5.0, minutos_atras=30)
    desde, hasta = AHORA - timedelta(hours=1), AHORA + timedelta(minutes=1)
    for inicio, fin in ((desde, hasta), (AHORA - timedelta(days=9), AHORA - timedelta(days=8))):
        respuesta = client.get(
            "/api/trazabilidad/verificar-dispositivo",
            params={"device_id": DEVICE, "desde": inicio.isoformat(), "hasta": fin.isoformat()},
            headers=auth_header(token_farmaceutico),
        )
        assert respuesta.status_code == 200

    veredicto = (await _reporte(db_session_factory, desde, hasta))["veredicto_integridad"]

    assert len(veredicto["digest_cadena"]) == 64
    int(veredicto["digest_cadena"], 16)
    ultima = veredicto["ultima_verificacion"]
    # La verificación de hace 8 días es más reciente en la cadena, pero no cubre el periodo.
    assert datetime.fromisoformat(ultima["desde"]) == desde
    assert ultima["resultado"] == "integra"
    assert len(ultima["hash_eslabon"]) == 64
    texto = _textos(GeneradorReporteBPAPDF()._seccion_integridad(veredicto))
    assert veredicto["digest_cadena"] in texto
    assert ultima["hash_eslabon"] in texto
    assert "Última verificación por dispositivo y periodo" in texto


def test_la_seccion_de_integridad_distingue_no_concluida_y_no_sobredeclara():
    generador = GeneradorReporteBPAPDF()
    digest = "a" * 64
    integra = _textos(generador._seccion_integridad(
        {"estado": "integra", "integra": True, "total_registros": 3, "verificado_en": "x", "digest_cadena": digest,
         "chain_id": DEVICE, "alcance": "No equivale a inmutabilidad. DIGEMID."}
    ))
    assert digest in integra
    assert "ningún registro del período fue alterado" not in integra  # sobredeclaración retirada
    assert "No se detectaron alteraciones" in integra
    no_concluida = _textos(generador._seccion_integridad(
        {"estado": "no_concluida", "integra": False, "total_no_verificables": 4, "verificado_en": "x",
         "digest_cadena": digest, "ultima_verificacion": None}
    ))
    assert "no concluida" in no_concluida.lower()
    assert "No consta una verificación por dispositivo y periodo" in no_concluida


def test_el_cierre_y_la_marca_son_coldcare():
    texto = _textos(GeneradorReporteBPAPDF()._cierre())
    assert "ColdCare" in texto and "ThermoTrace" not in texto
    assert "no declara por sí mismo cumplimiento sanitario integral" in texto
    assert "DIGEMID" in texto
    checklist = _textos(GeneradorReporteBPAPDF()._seccion_checklist([]))
    assert "(HU-37)" not in checklist


# ── HU-51 esc. 4: ubicación vigente durante el periodo ────────────────────


async def test_un_cambio_posterior_al_periodo_no_altera_la_ubicacion_reportada(
    client, token_admin, token_farmaceutico, db_session_factory
):
    alta = client.post(
        "/api/dispositivos",
        json={"device_id": DEVICE, "ubicacion": "Estante 1", "sensores_habilitados": ["ds18b20"]},
        headers=auth_header(token_admin),
    )
    assert alta.status_code == 201
    cambio = client.patch(
        f"/api/dispositivos/{DEVICE}/instalacion",
        json={"ubicacion": "Estante 2", "fecha_instalacion": "2026-11-01"},
        headers=auth_header(token_admin),
    )
    assert cambio.status_code == 200

    anterior = (await _reporte(db_session_factory, AHORA - timedelta(days=3), AHORA - timedelta(days=1)))["equipos"]
    [equipo] = anterior
    assert equipo["ubicacion"] == "Estante 2"  # la ficha actual…
    assert equipo["ubicacion_periodo"] == "Estante 1"  # …pero en el periodo regía la anterior
    assert equipo["fecha_instalacion_periodo"] is None
    assert equipo["cambios_posteriores_al_periodo"] >= 1
    assert equipo["cambios_ubicacion"] == []

    [actual] = (await _reporte(db_session_factory, AHORA - timedelta(days=1), AHORA + timedelta(hours=1)))["equipos"]
    assert actual["ubicacion_inicio_periodo"] == "Estante 1"
    assert actual["ubicacion_periodo"] == "Estante 2"
    assert actual["fecha_instalacion_periodo"] == "2026-11-01"
    assert actual["instalado_por_nombre"] == "Admin Test"
    assert actual["cambios_historial_total"] >= 2

    texto = _textos(GeneradorReporteBPAPDF()._seccion_equipos([equipo, actual]))
    assert "Estante 1 → Estante 2" in texto
    assert "por Admin Test" in texto
    assert "historial-configuracion" in texto
    assert "cambió después del período" in texto


@pytest.mark.parametrize("clave_en_la_descarga", [True, False])
def test_el_pdf_se_compone_con_las_secciones_nuevas(
    client, token_admin, token_tecnico, token_auditor, monkeypatch, clave_en_la_descarga
):
    """Maquetación real (ReportLab) con equipo, verificación HU-37 citada y,
    sin clave HMAC al descargar, el estado `no_concluida`."""
    from src.infrastructure.config import get_settings

    monkeypatch.setattr(get_settings(), "trazabilidad_hmac_key", "k" * 40)
    client.post(
        "/api/dispositivos",
        json={"device_id": DEVICE, "ubicacion": "Estante <1>", "sensores_habilitados": ["ds18b20"]},
        headers=auth_header(token_admin),
    )
    client.patch(f"/api/dispositivos/{DEVICE}/instalacion", json={"ubicacion": "Estante 2"}, headers=auth_header(token_admin))
    _ingestar(client, token_tecnico, 9.0, minutos_atras=20)
    desde, hasta = AHORA - timedelta(hours=1), AHORA + timedelta(hours=1)
    client.get(
        "/api/trazabilidad/verificar-dispositivo",
        params={"device_id": DEVICE, "desde": desde.isoformat(), "hasta": hasta.isoformat()},
        headers=auth_header(token_auditor),
    )
    if not clave_en_la_descarga:
        monkeypatch.setattr(get_settings(), "trazabilidad_hmac_key", None)

    respuesta = client.get(
        "/api/reportes/bpa/pdf",
        params={"fecha_desde": desde.isoformat(), "fecha_hasta": hasta.isoformat(), "device_id": DEVICE},
        headers=auth_header(token_auditor),
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.content.startswith(b"%PDF-")

"""HU-23 esc. 3: la máquina de estados es PENDIENTE -> RECONOCIDA -> ATENDIDA;
la entidad no permite saltar de PENDIENTE a ATENDIDA."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.domain.entities.alerta_termica import AlertaTermica
from src.domain.exceptions import DomainError
from src.domain.value_objects.estado_alerta import EstadoAlerta
from src.domain.value_objects.nivel_riesgo import NivelRiesgo

AHORA = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def _alerta(estado: EstadoAlerta = EstadoAlerta.PENDIENTE) -> AlertaTermica:
    return AlertaTermica(
        reading_id=uuid4(), device_id="ESP32-01", nivel_riesgo=NivelRiesgo.EXCURSION_CRITICA, mensaje="m", estado=estado
    )


def test_una_alerta_pendiente_no_salta_a_atendida():
    alerta = _alerta()
    with pytest.raises(DomainError, match="reconocerse antes"):
        alerta.marcar_atendida(AHORA)
    assert alerta.estado == EstadoAlerta.PENDIENTE
    assert alerta.atendida_en is None


def test_el_flujo_completo_registra_quien_y_cuando():
    alerta = _alerta()
    qf = uuid4()
    alerta.reconocer(qf, AHORA)
    alerta.marcar_atendida(AHORA)
    assert (alerta.estado, alerta.revisada_por, alerta.reconocida_en, alerta.atendida_en) == (
        EstadoAlerta.ATENDIDA, qf, AHORA, AHORA,
    )


def test_una_alerta_atendida_no_se_vuelve_a_atender():
    with pytest.raises(DomainError, match="ya fue atendida"):
        _alerta(EstadoAlerta.ATENDIDA).marcar_atendida(AHORA)

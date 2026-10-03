import pytest

from src.domain.value_objects.rango_termico import RANGO_TERMICO_BPA, RangoTermico


def test_rango_valido_por_defecto_es_2_a_8():
    assert RANGO_TERMICO_BPA.minimo_celsius == 2.0
    assert RANGO_TERMICO_BPA.maximo_celsius == 8.0


@pytest.mark.parametrize("temperatura", [2.0, 5.0, 8.0])
def test_contiene_temperaturas_dentro_del_rango(temperatura):
    assert RANGO_TERMICO_BPA.contiene(temperatura) is True


@pytest.mark.parametrize("temperatura", [1.9, 8.1, -5.0, 20.0])
def test_no_contiene_temperaturas_fuera_del_rango(temperatura):
    assert RANGO_TERMICO_BPA.contiene(temperatura) is False


def test_distancia_al_limite_es_cero_en_los_bordes():
    assert RANGO_TERMICO_BPA.distancia_al_limite(2.0) == 0.0
    assert RANGO_TERMICO_BPA.distancia_al_limite(8.0) == 0.0


def test_distancia_al_limite_fuera_de_rango():
    assert RANGO_TERMICO_BPA.distancia_al_limite(10.0) == 2.0
    assert RANGO_TERMICO_BPA.distancia_al_limite(0.0) == 2.0


def test_rango_invalido_lanza_error():
    with pytest.raises(ValueError):
        RangoTermico(minimo_celsius=8.0, maximo_celsius=2.0)

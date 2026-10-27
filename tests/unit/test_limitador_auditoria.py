"""HU-44: tope de filas de auditoría durante la retención."""

from src.interface.limitador_auditoria import LimitadorAuditoria


def test_una_fila_por_ventana_y_el_resto_se_cuenta():
    limitador = LimitadorAuditoria(ventana_segundos=60)
    assert limitador.permitir("pop_fallida", "FARM-01", "firma_invalida") == 0
    assert limitador.permitir("pop_fallida", "FARM-01", "firma_invalida") is None
    assert limitador.permitir("pop_fallida", "FARM-01", "nonce_desconocido") == 0


def test_vaciar_devuelve_lo_contado_solo_del_dispositivo_y_lo_olvida():
    limitador = LimitadorAuditoria(ventana_segundos=60)
    for _ in range(3):
        limitador.permitir("pop_fallida", "FARM-01", "firma_invalida")
    limitador.permitir("lectura_retenida", "FARM-01")
    for _ in range(2):
        limitador.permitir("lectura_retenida", "FARM-02")

    assert limitador.vaciar("FARM-01") == {"pop_fallida:firma_invalida": 2}
    assert limitador.vaciar("FARM-01") == {}
    # La primera fila de la siguiente retención vuelve a salir.
    assert limitador.permitir("pop_fallida", "FARM-01", "firma_invalida") == 0
    assert limitador.vaciar("FARM-02") == {"lectura_retenida": 1}

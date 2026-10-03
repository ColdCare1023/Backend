"""JtiStore: revocación de access tokens y consumo único de tickets SSE."""

import time

from src.infrastructure.security.revocation_store import JtiStore


def test_jti_registrado_queda_revocado():
    store = JtiStore()
    store.registrar("abc", time.time() + 60)
    assert store.contiene("abc") is True
    assert store.contiene("otro") is False


def test_jti_expirado_se_purga_solo():
    store = JtiStore()
    store.registrar("efimero", time.time() - 1)
    assert store.contiene("efimero") is False


def test_consumir_es_de_un_solo_uso():
    store = JtiStore()
    exp = time.time() + 60
    assert store.consumir("ticket-1", exp) is True
    assert store.consumir("ticket-1", exp) is False


def test_consumir_tickets_distintos_no_interfieren():
    store = JtiStore()
    exp = time.time() + 60
    assert store.consumir("t1", exp) is True
    assert store.consumir("t2", exp) is True


def test_store_acota_entradas_y_conserva_la_mas_reciente():
    store = JtiStore(max_entradas=2)
    exp = time.time() + 60
    store.registrar("uno", exp)
    store.registrar("dos", exp)
    store.registrar("tres", exp)
    assert store.contiene("uno") is False
    assert store.contiene("dos") is True
    assert store.contiene("tres") is True

"""B-2: reglas ACL explícitas por dispositivo para EMQX Serverless."""

from scripts.reglas_acl_emqx import reglas


def test_cada_nodo_solo_publica_y_escucha_sus_propios_topicos():
    filas = reglas(["FARM-01", "FARM-02"])
    del_nodo = [f for f in filas if f["usuario"] == "FARM-01"]

    assert {(f["accion"], f["topico"]) for f in del_nodo} == {
        ("publish", "farmacias/FARM-01/lecturas"), ("publish", "farmacias/FARM-01/eventos"),
        ("subscribe", "farmacias/FARM-01/ack"), ("subscribe", "farmacias/FARM-01/ota"),
    }
    assert all("FARM-02" not in f["topico"] for f in del_nodo)
    assert not any("${" in f["topico"] for f in filas)  # sin marcadores no verificados


def test_la_ultima_regla_deniega_todo_lo_demas():
    assert reglas([])[-1] == {"usuario": "(todos)", "accion": "all", "topico": "#", "permiso": "deny"}

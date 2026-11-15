"""HU-29 esc. 1 y 3 (P0-4 c): lo que el servicio cron de Railway necesita
está en el repositorio y es verificable sin tocar Railway.

- Dockerfile.respaldo trae pg_dump de la versión mayor elegida (PG_MAJOR),
  imagen fijada por digest, y el respaldo no corre como root.
- railway.respaldo.json declara la programación `0 7 * * *` (02:00 en Perú).
- La retención opcional del volumen nunca borra el respaldo recién hecho.
"""

import json
import re
from pathlib import Path

from scripts import respaldo_postgres

RAIZ = Path(__file__).resolve().parents[2]


def _instrucciones(texto: str) -> list[str]:
    unidas = re.sub(r"\\\n\s*", " ", texto)
    return [linea.strip() for linea in unidas.splitlines() if linea.strip() and not linea.strip().startswith("#")]


def test_la_imagen_de_respaldo_fija_la_base_y_el_cliente_postgresql():
    instrucciones = _instrucciones((RAIZ / "Dockerfile.respaldo").read_text(encoding="utf-8"))
    desde = [i for i in instrucciones if i.startswith("FROM ")]
    assert len(desde) == 1 and re.match(r"^FROM \S+@sha256:[0-9a-f]{64}$", desde[0])
    assert "ARG PG_MAJOR=17" in instrucciones
    instalacion = next(i for i in instrucciones if "postgresql-client-" in i)
    assert '"postgresql-client-${PG_MAJOR}"' in instalacion
    assert "apt.postgresql.org.sh" in instalacion
    assert "--require-hashes" in next(i for i in instrucciones if "pip install" in i)


def test_el_respaldo_corre_sin_root_y_sale_al_terminar():
    instrucciones = _instrucciones((RAIZ / "Dockerfile.respaldo").read_text(encoding="utf-8"))
    (cmd,) = [i for i in instrucciones if i.startswith("CMD ")]
    assert "setpriv --reuid=app --regid=app" in cmd
    assert "python -m scripts.respaldo_postgres" in cmd
    assert any(i.startswith("RUN useradd") and "10001" in i for i in instrucciones)
    # Ni uvicorn ni un proceso que quede vivo: Railway salta la ejecución siguiente.
    assert "uvicorn" not in cmd


def test_la_configuracion_del_cron_de_railway():
    config = json.loads((RAIZ / "railway.respaldo.json").read_text(encoding="utf-8"))
    assert config["build"] == {"builder": "DOCKERFILE", "dockerfilePath": "Dockerfile.respaldo"}
    assert config["deploy"]["cronSchedule"] == "0 7 * * *"
    assert config["deploy"]["restartPolicyType"] == "NEVER"
    assert (RAIZ / config["build"]["dockerfilePath"]).exists()


def test_el_contexto_de_build_incluye_los_scripts_de_respaldo():
    ignorados = {
        linea.strip() for linea in (RAIZ / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if linea.strip() and not linea.startswith("#")
    }
    assert not {"scripts", "scripts/", "src", "src/"} & ignorados


def _crear(destino: Path, marca: str) -> None:
    for sufijo in (".dump", ".manifiesto.json", ".restauracion.json"):
        (destino / f"coldcare_{marca}{sufijo}").write_text("x", encoding="utf-8")


def test_la_retencion_conserva_los_mas_recientes(tmp_path):
    for marca in ("20261110T070000Z", "20261111T070000Z", "20261112T070000Z", "20261113T070000Z"):
        _crear(tmp_path, marca)

    eliminados = respaldo_postgres.podar_respaldos(tmp_path, retener=2)

    assert eliminados == ["coldcare_20261111T070000Z.dump", "coldcare_20261110T070000Z.dump"]
    quedan = sorted(p.name for p in tmp_path.iterdir())
    assert all("20261112" in nombre or "20261113" in nombre for nombre in quedan)
    assert len(quedan) == 6


def test_sin_retencion_no_se_borra_nada(tmp_path):
    _crear(tmp_path, "20261110T070000Z")
    assert respaldo_postgres.podar_respaldos(tmp_path, retener=0) == []
    assert len(list(tmp_path.iterdir())) == 3


def test_un_respaldo_fallido_no_poda(monkeypatch, tmp_path):
    for marca in ("20261110T070000Z", "20261111T070000Z"):
        _crear(tmp_path, marca)
    monkeypatch.setattr(respaldo_postgres.shutil, "which", lambda _nombre: None)  # sin pg_dump
    monkeypatch.setattr(respaldo_postgres.logger, "disabled", False)

    codigo = respaldo_postgres.main(["--destino", str(tmp_path), "--retener", "1"])

    assert codigo == respaldo_postgres.CODIGO_FALLO
    assert len(list(tmp_path.glob("*.dump"))) == 2

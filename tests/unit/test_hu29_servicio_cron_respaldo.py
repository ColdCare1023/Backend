"""HU-29 esc. 1 y 3 (P0-4 c): lo que el servicio cron de Railway necesita
está en el repositorio y es verificable sin tocar Railway.

- Dockerfile.respaldo trae pg_dump de la versión mayor elegida (PG_MAJOR; por
  omisión 18, la del servidor de producción), imagen fijada por digest, y el
  respaldo no corre como root ni con el HOME de root.
- railway.respaldo.json declara la programación `0 7 * * *` (02:00 en Perú).
- La retención opcional del volumen nunca borra el respaldo recién hecho.
"""

import json
import re
import shlex
from pathlib import Path

from scripts import respaldo_postgres

RAIZ = Path(__file__).resolve().parents[2]


def _instrucciones(texto: str) -> list[str]:
    unidas = re.sub(r"\\\n\s*", " ", texto)
    return [linea.strip() for linea in unidas.splitlines() if linea.strip() and not linea.strip().startswith("#")]


def _comando_de_respaldo() -> list[str]:
    """El `exec` con el que el arranque (root) lanza el respaldo, en tokens."""
    instrucciones = _instrucciones((RAIZ / "Dockerfile.respaldo").read_text(encoding="utf-8"))
    (cmd,) = [i for i in instrucciones if i.startswith("CMD ")]
    forma_exec = json.loads(cmd.removeprefix("CMD "))
    assert forma_exec[:2] == ["sh", "-c"]
    (lanzamiento,) = [paso.strip() for paso in forma_exec[2].split("&&") if paso.strip().startswith("exec ")]
    return shlex.split(lanzamiento)


def test_la_imagen_de_respaldo_fija_la_base_y_el_cliente_postgresql():
    texto = (RAIZ / "Dockerfile.respaldo").read_text(encoding="utf-8")
    instrucciones = _instrucciones(texto)
    desde = [i for i in instrucciones if i.startswith("FROM ")]
    assert len(desde) == 1 and re.match(r"^FROM \S+@sha256:[0-9a-f]{64}$", desde[0])
    # Producción es PostgreSQL 18 (18.6 el 15/11/2026): un pg_dump 17 se niega
    # a volcarlo, así que la imagen trae el 18 aunque falte la variable.
    assert "ARG PG_MAJOR=18" in instrucciones
    assert re.search(r"producción[^.]*PostgreSQL 18", texto)
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


def test_el_respaldo_corre_con_un_home_que_app_puede_usar():
    """setpriv cambia el usuario pero no HOME. Con el HOME=/root del arranque,
    la primera ejecución en Railway (15/11/2026) falló con `[Errno 13]
    Permission denied: '/root/.postgresql/postgresql.key'`: el driver de
    PostgreSQL busca ahí la clave del cliente y `app` no puede entrar en
    /root. La imagen fija HOME para el proceso de respaldo, sin depender de
    una variable de Railway y sin vaciar el resto del entorno."""
    comando = _comando_de_respaldo()
    python = comando.index("python")
    assert comando[:2] == ["exec", "env"]
    asignaciones = {}
    for token in comando[2:python]:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
            break
        nombre, valor = token.split("=", 1)
        asignaciones[nombre] = valor
    # /tmp: el valor que arregló la ejecución en producción. Fijo, no una
    # referencia a otra variable, y fuera de /root.
    assert asignaciones.get("HOME") == "/tmp"
    # setpriv recibe ese entorno y lanza el respaldo como `app`.
    assert comando[comando.index("setpriv"):python][:3] == ["setpriv", "--reuid=app", "--regid=app"]
    assert comando.index("env") < comando.index("setpriv") < python
    # Ni `setpriv --reset-env` ni `env -i`: borrarían DATABASE_URL,
    # RESPALDO_DESTINO y RESPALDO_RETENER.
    assert not {"--reset-env", "-i", "--ignore-environment", "-"} & set(comando[:python])


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


def test_descartar_incompletos_solo_borra_lo_que_quedo_a_medias(tmp_path):
    """Un respaldo correcto y una ejecución fallida con la misma marca (mismo
    segundo): solo se borran los `.parcial` de la fallida, nunca el `.dump` y
    el manifiesto terminados."""
    marca = "20261115T071801Z"
    terminados = {f"coldcare_{marca}.dump", f"coldcare_{marca}.manifiesto.json"}
    for nombre in terminados | {f"{nombre}.parcial" for nombre in terminados}:
        (tmp_path / nombre).write_text("x", encoding="utf-8")

    descartados = respaldo_postgres.descartar_incompletos(tmp_path, marca)

    assert sorted(descartados) == sorted(f"{nombre}.parcial" for nombre in terminados)
    assert {p.name for p in tmp_path.iterdir()} == terminados


def test_la_retencion_no_cuenta_un_parcial_de_una_ejecucion_interrumpida(tmp_path):
    """Un proceso muerto a mitad del pg_dump (SIGKILL) no pasa por el manejo
    del fallo y deja su `.dump.parcial`: la poda no lo cuenta ni lo toma por
    un respaldo, así que no borra uno bueno a cambio."""
    for marca in ("20261111T070000Z", "20261112T070000Z"):
        _crear(tmp_path, marca)
    (tmp_path / "coldcare_20261113T070000Z.dump.parcial").write_bytes(b"PGDMP")

    assert respaldo_postgres.podar_respaldos(tmp_path, retener=2) == []
    assert len(list(tmp_path.glob("coldcare_*.dump"))) == 2


def test_un_respaldo_fallido_no_poda(monkeypatch, tmp_path):
    for marca in ("20261110T070000Z", "20261111T070000Z"):
        _crear(tmp_path, marca)
    monkeypatch.setattr(respaldo_postgres.shutil, "which", lambda _nombre: None)  # sin pg_dump
    monkeypatch.setattr(respaldo_postgres.logger, "disabled", False)

    codigo = respaldo_postgres.main(["--destino", str(tmp_path), "--retener", "1"])

    assert codigo == respaldo_postgres.CODIGO_FALLO
    assert len(list(tmp_path.glob("*.dump"))) == 2


# ── Documentación operativa (P2 y P0-2 de la revalidación) ──────────────────


def _seccion(texto: str, titulo: str) -> str:
    """Cuerpo de la sección `titulo` (hasta el siguiente encabezado)."""
    inicio = texto.index(titulo) + len(titulo)
    siguiente = re.search(r"^#{1,6} ", texto[inicio:], flags=re.MULTILINE)
    return texto[inicio: inicio + siguiente.start()] if siguiente else texto[inicio:]


def test_la_verificacion_de_restauracion_documenta_las_9_tablas():
    from scripts import verificar_restauracion
    from src.infrastructure.respaldo.huellas import TABLAS_EVIDENCIA

    documentacion = verificar_restauracion.__doc__
    assert len(TABLAS_EVIDENCIA) == 9
    assert "9 tablas" in documentacion
    assert [tabla for tabla in TABLAS_EVIDENCIA if tabla not in documentacion] == []


def test_la_guia_de_railway_da_la_retencion_efectiva_de_los_backups_nativos():
    """HU-29 esc. 1 pide documentar la retención efectiva: Railway conserva
    los backups Daily 6 días, los Weekly 27 y los Monthly 89."""
    guia = (RAIZ / "docs/operacion/RESPALDO_RAILWAY.md").read_text(encoding="utf-8")
    seccion = _seccion(guia, "## 1. Respaldo nativo de Railway (escenario 1)")
    for programa, dias in (("Daily", 6), ("Weekly", 27), ("Monthly", 89)):
        assert re.search(rf"\*\*{programa}\*\*[^.]*\b{dias} días", seccion), programa
    assert "~1 mes" not in seccion and "alrededor de" not in seccion


def test_el_fallo_provocado_de_la_guia_llega_al_log_del_backend():
    """HU-29 esc. 3 («alerta operativa crítica en el log del backend»): con
    una contraseña errónea el cron no puede escribir RESPALDO_FALLIDO y el
    vigilante de la API no ve nada. La guía provoca el fallo con la base
    accesible: un PG_MAJOR anterior al del servidor, que
    `verificar_cliente_compatible` rechaza antes de volcar."""
    guia = (RAIZ / "docs/operacion/RESPALDO_RAILWAY.md").read_text(encoding="utf-8")
    escenario = _seccion(guia, "### Escenario 3 (fallo provocado)")
    assert "PG_MAJOR" in escenario
    assert "RESPALDO_FALLIDO" in escenario
    assert "RESPALDO_MAX_ANTIGUEDAD_HORAS=26" in escenario
    assert 'respaldos.estado = "fallido"' in escenario
    # El texto que se busca en el log del backend es el que escribe el vigilante.
    assert "ALERTA OPERATIVA: el último respaldo programado de PostgreSQL falló" in escenario
    assert "ALERTA OPERATIVA: el último respaldo programado de PostgreSQL falló" in (
        RAIZ / "src/interface/tareas.py"
    ).read_text(encoding="utf-8")
    # El procedimiento ya no es la contraseña errónea (solo se menciona para
    # explicar por qué no sirve).
    assert not re.search(r"^1\. .*contraseña errónea", escenario, flags=re.MULTILINE)


def test_la_guia_del_cron_sigue_la_rama_de_despliegue_hasta_el_corte_de_config_as_code():
    """Railway ya no deja elegir /railway.respaldo.json en un servicio nuevo y
    sí lee el railway.json de la raíz, que es el de la API: el 15/11/2026 el
    cron construyó la API. El cron sigue la rama deploy/respaldo-postgres
    hasta el 2026-12-01, cuando Railway deja de leer config as code; antes de
    esa fecha el Backend tiene que tener en su panel lo que hoy le da
    railway.json."""
    guia = (RAIZ / "docs/operacion/RESPALDO_RAILWAY.md").read_text(encoding="utf-8")
    pasos = _seccion(guia, "## 2. Servicio cron `respaldo-postgres`")
    assert "`deploy/respaldo-postgres`" in pasos
    assert "Railway config file** ya no sirve" in pasos
    assert "`18` en producción" in pasos
    assert "`env HOME=/tmp`" in pasos
    rama = _seccion(guia, "### Rama de despliegue y fin de config as code")
    assert "2026-12-01" in rama
    assert "git merge --no-edit origin/main" in rama
    for ruta in ("scripts/respaldo_postgres.py", "src/infrastructure/respaldo/", "Dockerfile.respaldo"):
        assert f"`{ruta}`" in rama, ruta
    # La tabla de migración del Backend lleva los valores reales de railway.json.
    api = json.loads((RAIZ / "railway.json").read_text(encoding="utf-8"))
    for valor in (
        api["build"]["dockerfilePath"],
        api["deploy"]["startCommand"],
        *api["deploy"]["preDeployCommand"],
        api["deploy"]["healthcheckPath"],
    ):
        assert f"`{valor}`" in rama, valor
    assert f"{api['deploy']['healthcheckTimeout']} s" in rama
    assert f"{api['deploy']['restartPolicyMaxRetries']} reintentos" in rama


def test_el_criterio_de_aceptacion_de_hu29_pide_el_fallo_que_llega_al_backend():
    """El criterio verificable de HU-29 pedía provocar el Escenario 3 con una
    contraseña errónea y capturar solo el log del cron. Con la base
    inaccesible no hay RESPALDO_FALLIDO y el log del backend no alerta; el
    criterio es la alerta en el log del backend (RESPALDO_RAILWAY.md)."""
    guia = (RAIZ / "docs/operacion/HU-29_respaldo_y_restauracion.md").read_text(encoding="utf-8")
    criterio = _seccion(guia, "## Criterio de aceptación verificable")
    assert "PG_MAJOR" in criterio
    assert "ALERTA OPERATIVA" in criterio
    assert "RESPALDO_FALLIDO" in criterio
    assert 'respaldos.estado = "fallido"' in criterio
    # La contraseña errónea solo puede aparecer para decir que no sirve.
    for linea in re.split(r"\n(?=- )", criterio):
        if "contraseña errónea" in linea:
            assert "no sirve" in linea, linea

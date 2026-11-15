"""HU-18 P-2 / P-6: `corridas --registrar` se niega, con un mensaje en español,
código de salida 2 y sin escribir nada en el registro, si:

- el scikit-learn instalado no es el fijado en requirements.txt (P-6);
- la versión ya existe (el registro nunca sobrescribe) o no es registrable;
- las corridas o las etiquetas no alcanzan para ajustar, elegir el umbral
  fuera de muestra y evaluar.

Antes de esta ronda `corridas` no tenía `--registrar` (solo informaba), así
que todas estas llamadas terminaban en un error de argumentos."""

import json

import pytest
import sklearn

from src.infrastructure.ai.prediccion_preventiva import registro_corridas
from src.infrastructure.ai.prediccion_preventiva.__main__ import main
from tests.corridas_sinteticas import escribir_corridas
from tests.ia_preventiva import registrar_preventivo

VERSION = "rf-corridas-h30-rechazo-0.1.0"


def _registrar(csv, raiz, *extra, version=VERSION, prueba="C4,C5"):
    return main([
        "corridas", "--csv", str(csv), "--prueba", prueba, "--registrar", "--version", version,
        "--directorio-registro", str(raiz), *extra,
    ])


def _sin_rastro(raiz) -> bool:
    return not raiz.exists() or not any(raiz.iterdir())


@pytest.fixture
def csv(tmp_path):
    return escribir_corridas(tmp_path / "corridas.csv", corridas=5)


def test_scikit_learn_distinto_del_fijado_no_registra(csv, tmp_path, monkeypatch, capsys):
    fijada = registro_corridas.version_sklearn_fijada()
    monkeypatch.setattr(sklearn, "__version__", "1.9.1" if fijada != "1.9.1" else "1.9.0")
    raiz = tmp_path / "registro"

    assert _registrar(csv, raiz) == 2

    error = capsys.readouterr().err
    assert error.startswith("No se registra el modelo: el scikit-learn instalado")
    assert f"no es el fijado en requirements.txt ({fijada})" in error and "P-6" in error
    assert _sin_rastro(raiz)


def test_sin_version_fijada_en_requirements_no_registra(csv, tmp_path, monkeypatch, capsys):
    requisitos = tmp_path / "requirements.txt"
    requisitos.write_text("scikit-learn>=1.9\n", encoding="utf-8")
    monkeypatch.setattr(registro_corridas, "ARCHIVO_REQUISITOS", requisitos)
    raiz = tmp_path / "registro"

    assert _registrar(csv, raiz) == 2

    assert "no fija scikit-learn con '=='" in capsys.readouterr().err
    assert _sin_rastro(raiz)


def test_el_entorno_de_pruebas_pasa_la_guarda_p6():
    assert registro_corridas.exigir_sklearn_fijado() == sklearn.__version__


def test_una_version_existente_no_se_sobrescribe(csv, tmp_path, capsys):
    raiz = tmp_path / "registro"
    registrar_preventivo(raiz, VERSION, aprobar=False)
    antes = {p.name: p.read_bytes() for p in (raiz / "historial.json", raiz / VERSION / "manifiesto.json",
                                               raiz / VERSION / "modelo.joblib")}

    assert _registrar(csv, raiz) == 2

    assert f"la versión {VERSION} ya existe en el registro" in capsys.readouterr().err
    despues = {p.name: p.read_bytes() for p in (raiz / "historial.json", raiz / VERSION / "manifiesto.json",
                                                 raiz / VERSION / "modelo.joblib")}
    assert despues == antes
    assert len(json.loads(antes["historial.json"])) == 1


def test_una_version_que_figura_en_el_historial_no_se_reutiliza_aunque_falte_su_directorio(csv, tmp_path, capsys):
    """Antes solo se miraba el directorio: sin él, la CLI registraba V otra
    vez con otro hash y historial.json quedaba con dos `registrado` de V (la
    base, que ya conoce V, la rechaza con el hash viejo)."""
    import shutil

    raiz = tmp_path / "registro"
    assert _registrar(csv, raiz) == 0
    capsys.readouterr()
    shutil.rmtree(raiz / VERSION)
    historial = (raiz / "historial.json").read_bytes()

    assert _registrar(csv, raiz) == 2

    error = capsys.readouterr().err
    assert error.startswith("No se registra el modelo: ")
    assert f"la versión {VERSION} ya existe en el registro: figura en historial.json" in error
    assert (raiz / "historial.json").read_bytes() == historial
    assert [e["evento"] for e in json.loads(historial)] == ["registrado"]
    assert not (raiz / VERSION).exists()


def test_un_historial_ilegible_no_deja_registrar(csv, tmp_path, capsys):
    """Antes se escribían el artefacto y el manifiesto y la CLI terminaba con
    una traza al anexar a un historial.json que no es JSON (versión a medias)."""
    raiz = tmp_path / "registro"
    raiz.mkdir()
    (raiz / "historial.json").write_text("{no es json", encoding="utf-8")

    assert _registrar(csv, raiz) == 2

    error = capsys.readouterr().err
    assert "No se pudo leer historial.json" in error and "no se puede comprobar que la versión" in error
    assert sorted(p.name for p in raiz.iterdir()) == ["historial.json"]


@pytest.mark.parametrize(
    ("version", "texto"),
    [
        ("x" * 51, "50 como máximo"),
        ("Version_Mayusculas", "Nombre de versión inválido"),
    ],
)
def test_version_no_registrable(csv, tmp_path, capsys, version, texto):
    raiz = tmp_path / "registro"

    assert _registrar(csv, raiz, version=version) == 2

    assert texto in capsys.readouterr().err
    assert _sin_rastro(raiz)


@pytest.mark.parametrize(
    ("corridas", "con_excursion", "prueba", "texto"),
    [
        pytest.param(3, None, "C2,C3", "al menos 2 corridas de ajuste", id="una_corrida_de_ajuste"),
        pytest.param(5, {4, 5}, "C4,C5", "entrenamiento sin ambas clases", id="ajuste_sin_excursiones"),
        pytest.param(5, {1, 4, 5}, "C4,C5", "sin umbral fuera de muestra", id="una_sola_excursion_de_ajuste"),
        pytest.param(5, {1, 2, 3}, "C4,C5", "no tienen ambas clases", id="prueba_sin_excursiones"),
    ],
)
def test_pocas_corridas_o_etiquetas_no_registran(tmp_path, capsys, corridas, con_excursion, prueba, texto):
    csv = escribir_corridas(tmp_path / "corridas.csv", corridas=corridas, con_excursion=con_excursion)
    raiz = tmp_path / "registro"

    assert _registrar(csv, raiz, prueba=prueba) == 2

    error = capsys.readouterr().err
    assert error.startswith("No se registra el modelo: ")
    assert texto in error
    assert _sin_rastro(raiz)


def test_corrida_de_prueba_inexistente_no_registra(csv, tmp_path, capsys):
    raiz = tmp_path / "registro"

    assert _registrar(csv, raiz, prueba="C9") == 2

    assert "Corridas de prueba inexistentes" in capsys.readouterr().err
    assert _sin_rastro(raiz)


@pytest.mark.parametrize(
    "argv",
    [
        ["--registrar"],
        ["--version", VERSION],
    ],
    ids=["registrar_sin_version", "version_sin_registrar"],
)
def test_argumentos_incoherentes(csv, tmp_path, capsys, argv):
    with pytest.raises(SystemExit) as salida:
        main(["corridas", "--csv", str(csv), "--prueba", "C4,C5", *argv])

    assert salida.value.code == 2
    assert "--registrar" in capsys.readouterr().err


def test_sin_registrar_corridas_sigue_siendo_un_informe(csv, tmp_path, monkeypatch, capsys):
    from src.infrastructure.ai.prediccion_preventiva import registro

    raiz = tmp_path / "registro_por_defecto"
    monkeypatch.setattr(registro, "RAIZ_REGISTRO", raiz)
    salida = tmp_path / "informe.json"

    assert main(["corridas", "--csv", str(csv), "--prueba", "C4,C5", "--salida", str(salida)]) == 0

    impreso = json.loads(capsys.readouterr().out)
    assert {c: u["metodo"] for c, u in impreso["umbral"].items()} == {
        "termico_minimo": "dejar_una_corrida_fuera", "termico_ambiente": "dejar_una_corrida_fuera",
    }
    informe = json.loads(salida.read_text(encoding="utf-8"))
    assert "_modelo" not in informe["resultados"]["termico_minimo"] and "_lecturas" not in informe
    assert not raiz.exists()


def test_en_el_informe_una_validacion_que_es_de_prueba_sale_con_codigo_2(csv, capsys):
    assert main(["corridas", "--csv", str(csv), "--prueba", "C4,C5", "--validacion", "C4"]) == 2

    assert "validación que no son de entrenamiento" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "texto"),
    [
        (["--prueba", "C9"], "Corridas de prueba inexistentes: ['C9']"),
        (["--prueba", "C4,C5", "--validacion", "C9"], "Corridas de validación inexistentes: ['C9']"),
    ],
    ids=["prueba_inexistente", "validacion_inexistente"],
)
def test_en_el_informe_una_corrida_inexistente_sigue_saliendo_con_codigo_2(csv, capsys, argv, texto):
    """Que la ablación con el SHT31 ya no detenga el informe no oculta los
    errores del operador: una corrida que no está en el CSV sigue siendo código 2."""
    assert main(["corridas", "--csv", str(csv), *argv]) == 2

    assert texto in capsys.readouterr().err

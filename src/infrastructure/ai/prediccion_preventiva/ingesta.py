"""Importadores de las fuentes externas al esquema canónico de lecturas.

Esquema canónico (un DataFrame por fuente):

    fuente               str   hohenheim | hospital | coldcare
    entorno_id           str   recinto físico: sensores del mismo recinto
                               comparten eventos y NO son independientes
    serie_id             str   sensor o equipo
    timestamp            datetime64[ns, UTC]
    temperatura_interna  float °C (NaN = sin lectura válida)
    puerta_abierta       float 1/0, NaN si la fuente no tiene la señal

Los archivos externos se tratan como NO confiables: los ZIP se leen en
memoria (nunca se extraen a disco, así que un nombre con `..` no puede
escribir fuera de ningún directorio) con límites de tamaño, de cantidad de
miembros y de razón de compresión; los CSV se validan contra el esquema
esperado y cada fila descartada queda contada con su motivo.
"""

import csv
import io
import stat
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from pathlib import Path, PurePosixPath

import numpy as np
import pandas as pd

COLUMNAS_CANONICAS = ("fuente", "entorno_id", "serie_id", "timestamp", "temperatura_interna", "puerta_abierta")

LIMITE_MIEMBROS_ZIP = 64
LIMITE_BYTES_MIEMBRO = 200 * 1024 * 1024
LIMITE_BYTES_TOTAL_ZIP = 1024 * 1024 * 1024
# Un CSV de texto comprime ~5-10x; 200x ya es la firma de una bomba ZIP.
LIMITE_RAZON_COMPRESION = 200
LIMITE_BYTES_CSV = 200 * 1024 * 1024

# Hohenheim no declara zona horaria. Las aperturas de puerta del protocolo
# caen en horario laboral alemán, así que se interpreta como hora local de
# Alemania: CEST (UTC+2) durante todo el periodo 2024-09-02 → 2024-09-11
# (el cambio de horario de 2024 fue el 27 de octubre). Supuesto documentado
# en docs/ml/01_DATASET_AUDIT.md.
ZONA_HOHENHEIM = timezone(timedelta(hours=2))
ENTORNO_HOHENHEIM = "hohenheim_camara_frigorifica"
FORMATO_FECHA_HOHENHEIM = "%d.%m.%Y %H:%M:%S"

# Cabecera publicada del CSV hospitalario: 9 nombres. La mayoría de filas
# trae 12 campos; los 3 finales no tienen nombre en el archivo y su
# significado se INFIERE de los valores (ver auditoría): se conservan con
# nombres que dejan explícito que no están documentados y no se usan como
# variables del modelo.
CABECERA_HOSPITAL = (
    "_id", "__v", "createdAt", "datetime", "doorStatus", "dsTemperature", "humidity", "time_slot", "username",
)
EXTRA_HOSPITAL = ("ref_dispositivo_sin_etiqueta", "temp_aux_sin_etiqueta", "updated_at_sin_etiqueta")


class ErrorArchivoInseguro(ValueError):
    """El archivo externo viola un límite de seguridad (ZIP bomb, path traversal, enlace simbólico)."""


class ErrorEsquemaDatos(ValueError):
    """El archivo no cumple el esquema esperado de la fuente."""


@dataclass(slots=True)
class InformeImportacion:
    fuente: str
    archivos: dict[str, int] = field(default_factory=dict)
    filas_leidas: int = 0
    filas_validas: int = 0
    descartes: Counter = field(default_factory=Counter)
    advertencias: list[str] = field(default_factory=list)

    def descartar(self, motivo: str, cantidad: int = 1) -> None:
        if cantidad:
            self.descartes[motivo] += int(cantidad)

    def como_dict(self) -> dict:
        return {
            "fuente": self.fuente,
            "archivos": self.archivos,
            "filas_leidas": self.filas_leidas,
            "filas_validas": self.filas_validas,
            "descartes": dict(self.descartes),
            "advertencias": self.advertencias,
        }


def leer_zip_seguro(
    ruta: Path,
    extensiones: tuple[str, ...] = (".csv",),
    limite_miembros: int = LIMITE_MIEMBROS_ZIP,
    limite_bytes_miembro: int = LIMITE_BYTES_MIEMBRO,
    limite_bytes_total: int = LIMITE_BYTES_TOTAL_ZIP,
    limite_razon: float = LIMITE_RAZON_COMPRESION,
) -> dict[str, bytes]:
    """Devuelve {nombre_base: contenido} de los miembros con extensión
    permitida. Rechaza el ZIP completo ante cualquier miembro sospechoso: un
    archivo con UNA entrada maliciosa no es una fuente confiable."""
    try:
        zf = zipfile.ZipFile(ruta)
    except zipfile.BadZipFile as exc:
        raise ErrorArchivoInseguro(f"{ruta.name}: ZIP corrupto o no es un ZIP ({exc})") from exc

    contenido: dict[str, bytes] = {}
    with zf:
        miembros = [i for i in zf.infolist() if not i.is_dir()]
        if len(miembros) > limite_miembros:
            raise ErrorArchivoInseguro(f"{ruta.name}: {len(miembros)} miembros supera el límite {limite_miembros}")
        total = 0
        for info in miembros:
            nombre = info.filename.replace("\\", "/")
            partes = PurePosixPath(nombre)
            if partes.is_absolute() or ".." in partes.parts or ":" in nombre:
                raise ErrorArchivoInseguro(f"{ruta.name}: ruta de miembro insegura {info.filename!r}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ErrorArchivoInseguro(f"{ruta.name}: el miembro {info.filename!r} es un enlace simbólico")
            if info.file_size > limite_bytes_miembro:
                raise ErrorArchivoInseguro(f"{ruta.name}: {info.filename!r} excede {limite_bytes_miembro} bytes")
            if info.compress_size and info.file_size / info.compress_size > limite_razon:
                raise ErrorArchivoInseguro(f"{ruta.name}: razón de compresión anómala en {info.filename!r}")
            total += info.file_size
            if total > limite_bytes_total:
                raise ErrorArchivoInseguro(f"{ruta.name}: el contenido descomprimido excede {limite_bytes_total} bytes")
            if partes.suffix.lower() not in extensiones:
                continue
            # La cabecera del ZIP puede mentir sobre el tamaño: se lee con
            # tope y se compara. zipfile verifica además el CRC al terminar.
            try:
                with zf.open(info) as fh:
                    datos = fh.read(info.file_size + 1)
            except (zipfile.BadZipFile, EOFError) as exc:
                raise ErrorArchivoInseguro(f"{ruta.name}: miembro corrupto {info.filename!r} ({exc})") from exc
            if len(datos) != info.file_size:
                raise ErrorArchivoInseguro(f"{ruta.name}: tamaño declarado falso en {info.filename!r}")
            base = partes.name
            if base in contenido:
                raise ErrorArchivoInseguro(f"{ruta.name}: miembro duplicado {base!r}")
            contenido[base] = datos
    return contenido


def _leer_texto_acotado(ruta: Path, limite: int = LIMITE_BYTES_CSV) -> str:
    if ruta.stat().st_size > limite:
        raise ErrorArchivoInseguro(f"{ruta.name}: excede {limite} bytes")
    return ruta.read_bytes().decode("utf-8-sig", errors="strict")


def detectar_separador(texto: str, candidatos: str = ";,\t") -> str:
    """Separador más frecuente en la primera línea no vacía (la cabecera).
    No usa csv.Sniffer: falla con filas de longitud irregular, justo el caso
    del CSV hospitalario (cabecera de 9 nombres, filas de 12 campos)."""
    primera = next((ln for ln in texto.splitlines() if ln.strip()), "")
    conteos = {c: primera.count(c) for c in candidatos}
    separador, n = max(conteos.items(), key=lambda kv: kv[1])
    if n == 0:
        raise ErrorEsquemaDatos("No se pudo determinar el separador del CSV")
    return separador


def a_epoch_ns(serie: pd.Series) -> np.ndarray:
    """Instantes como enteros de nanosegundos UTC. pandas 3 infiere la
    resolución (µs al parsear texto), así que nunca se asume `int64 == ns`."""
    utc = pd.to_datetime(serie, utc=True)
    return utc.dt.tz_convert(None).to_numpy().astype("datetime64[ns]").astype(np.int64)


# ── Hohenheim (Zenodo 10.5281/zenodo.15130001) ───────────────────


def parsear_csv_hohenheim(texto: str, serie_id: str, informe: InformeImportacion) -> pd.DataFrame:
    """CSV crudo del logger: `Date;Time;Temperature (C);Humidity (%)`, con la
    cabecera REPETIDA cada vez que el logger se reinició (el notebook de los
    autores las elimina por la presencia de 'Date')."""
    separador = detectar_separador(texto)
    lineas = [ln for ln in texto.splitlines() if ln.strip()]
    datos = [ln for ln in lineas if not ln.lstrip().startswith("Date")]
    cabeceras = len(lineas) - len(datos)
    if cabeceras == 0:
        raise ErrorEsquemaDatos(f"{serie_id}: falta la cabecera Date;Time;Temperature;Humidity")
    informe.descartar("cabecera_repetida", max(0, cabeceras - 1))

    filas = list(csv.reader(datos, delimiter=separador))
    informe.filas_leidas += len(filas)
    validas = [f for f in filas if len(f) == 4]
    informe.descartar("numero_columnas_inesperado", len(filas) - len(validas))
    if not validas:
        raise ErrorEsquemaDatos(f"{serie_id}: ninguna fila con 4 columnas (¿separador incorrecto?)")

    df = pd.DataFrame(validas, columns=["fecha", "hora", "temperatura", "humedad"])
    ts_local = pd.to_datetime(df["fecha"] + " " + df["hora"], format=FORMATO_FECHA_HOHENHEIM, errors="coerce")
    temperatura = pd.to_numeric(df["temperatura"], errors="coerce")
    informe.descartar("timestamp_invalido", int(ts_local.isna().sum()))
    informe.descartar("temperatura_no_numerica", int((temperatura.isna() & ts_local.notna()).sum()))
    ok = ts_local.notna()
    salida = pd.DataFrame(
        {
            "fuente": "hohenheim",
            "entorno_id": ENTORNO_HOHENHEIM,
            "serie_id": serie_id,
            "timestamp": ts_local[ok].dt.tz_localize(ZONA_HOHENHEIM).dt.tz_convert("UTC").reset_index(drop=True),
            "temperatura_interna": temperatura[ok].to_numpy(dtype=float),
            "puerta_abierta": np.nan,
        }
    )
    informe.filas_validas += int(salida["temperatura_interna"].notna().sum())
    return salida


def leer_acciones_hohenheim(ruta: Path) -> pd.DataFrame:
    """experiment_actions.csv → intervalos [inicio, fin) en UTC por acción."""
    texto = _leer_texto_acotado(ruta)
    df = pd.read_csv(io.StringIO(texto), sep=detectar_separador(texto), dtype=str)
    requeridas = {"Date", "Action", "Start Time", "End Time"}
    if not requeridas <= set(df.columns):
        raise ErrorEsquemaDatos(f"experiment_actions.csv: faltan columnas {sorted(requeridas - set(df.columns))}")
    inicio = pd.to_datetime(df["Date"] + " " + df["Start Time"], format="%Y-%m-%d %H:%M:%S", errors="coerce")
    fin = pd.to_datetime(df["Date"] + " " + df["End Time"], format="%Y-%m-%d %H:%M:%S", errors="coerce")
    if inicio.isna().any() or fin.isna().any():
        raise ErrorEsquemaDatos("experiment_actions.csv: fechas u horas inválidas")
    return pd.DataFrame(
        {
            "accion": df["Action"].str.strip(),
            "inicio": inicio.dt.tz_localize(ZONA_HOHENHEIM).dt.tz_convert("UTC"),
            "fin": fin.dt.tz_localize(ZONA_HOHENHEIM).dt.tz_convert("UTC"),
        }
    )


def marcar_puerta_hohenheim(lecturas: pd.DataFrame, acciones: pd.DataFrame) -> pd.DataFrame:
    """Estado de puerta del PROTOCOLO (no de un sensor) asignado a cada
    lectura: 1 dentro de un intervalo 'Door opened', 0 fuera. Solo se usa en
    la variante experimental con puerta; ColdCare tiene MC-38 opcional."""
    aperturas = acciones[acciones["accion"] == "Door opened"]
    ts = a_epoch_ns(lecturas["timestamp"])
    abierta = np.zeros(len(lecturas), dtype=float)
    for ini, fin in zip(a_epoch_ns(aperturas["inicio"]), a_epoch_ns(aperturas["fin"]), strict=True):
        abierta[(ts >= ini) & (ts < fin)] = 1.0
    salida = lecturas.copy()
    salida["puerta_abierta"] = abierta
    return salida


def importar_hohenheim(
    ruta_raw_zip: Path, ruta_acciones: Path | None = None
) -> tuple[pd.DataFrame, InformeImportacion, pd.DataFrame | None]:
    informe = InformeImportacion(fuente="hohenheim")
    miembros = leer_zip_seguro(ruta_raw_zip)
    if not miembros:
        raise ErrorEsquemaDatos(f"{ruta_raw_zip.name}: no contiene CSV")
    partes = []
    for nombre, datos in sorted(miembros.items()):
        serie_id = PurePosixPath(nombre).stem.upper()
        try:
            texto = datos.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ErrorEsquemaDatos(f"{nombre}: no es texto UTF-8") from exc
        filas_antes = informe.filas_leidas
        partes.append(parsear_csv_hohenheim(texto, serie_id, informe))
        informe.archivos[nombre] = informe.filas_leidas - filas_antes
    lecturas = pd.concat(partes, ignore_index=True)
    acciones = None
    if ruta_acciones is not None:
        acciones = leer_acciones_hohenheim(ruta_acciones)
        lecturas = marcar_puerta_hohenheim(lecturas, acciones)
    return lecturas, informe, acciones


# ── Hospital (Zenodo 10.5281/zenodo.22101912) ────────────────────


def importar_hospital(ruta_csv: Path) -> tuple[pd.DataFrame, InformeImportacion, pd.DataFrame]:
    """Devuelve (lecturas canónicas, informe, tabla completa con columnas
    auxiliares para la auditoría). Las filas de 12 campos se leen con los 3
    campos extra nombrados explícitamente como no documentados: leerlas con
    la cabecera de 9 nombres DESPLAZARÍA los valores (pandas usaría las
    primeras columnas como índice)."""
    informe = InformeImportacion(fuente="hospital")
    texto = _leer_texto_acotado(ruta_csv)
    filas = list(csv.reader(io.StringIO(texto), delimiter=detectar_separador(texto)))
    if not filas:
        raise ErrorEsquemaDatos(f"{ruta_csv.name}: vacío")
    cabecera, cuerpo = tuple(c.strip() for c in filas[0]), filas[1:]
    requeridas = {"datetime", "dsTemperature", "username", "doorStatus"}
    if not requeridas <= set(cabecera):
        raise ErrorEsquemaDatos(f"{ruta_csv.name}: faltan columnas {sorted(requeridas - set(cabecera))}")
    informe.filas_leidas = len(cuerpo)
    informe.archivos[ruta_csv.name] = len(cuerpo)

    n = len(cabecera)
    nombres_extendidos = list(cabecera) + list(EXTRA_HOSPITAL)
    conteo = Counter(len(f) for f in cuerpo)
    if conteo.get(n + len(EXTRA_HOSPITAL)):
        informe.advertencias.append(
            f"{conteo[n + len(EXTRA_HOSPITAL)]} filas traen {n + len(EXTRA_HOSPITAL)} campos con una cabecera de {n}: "
            "los 3 campos finales no están nombrados en el archivo."
        )
    normalizadas = []
    campos = []
    for fila in cuerpo:
        if len(fila) == n:
            normalizadas.append(fila + [""] * len(EXTRA_HOSPITAL))
        elif len(fila) == n + len(EXTRA_HOSPITAL):
            normalizadas.append(fila)
        else:
            informe.descartar("numero_columnas_inesperado")
            continue
        campos.append(len(fila))
    tabla = pd.DataFrame(normalizadas, columns=nombres_extendidos)
    tabla["campos"] = campos

    ts = pd.to_datetime(tabla["datetime"], utc=True, errors="coerce", format="ISO8601")
    temperatura = pd.to_numeric(tabla["dsTemperature"].replace("", np.nan), errors="coerce")
    puerta = tabla["doorStatus"].str.strip().str.lower().map({"open": 1.0, "closed": 0.0})
    informe.descartar("timestamp_invalido", int(ts.isna().sum()))
    informe.descartar("dsTemperature_vacia", int((tabla["dsTemperature"].str.strip() == "").sum()))
    informe.descartar("doorStatus_desconocido", int(puerta.isna().sum()))
    serie = tabla["username"].str.strip()
    informe.descartar("equipo_sin_identificador", int((serie == "").sum()))

    ok = ts.notna() & (serie != "")
    lecturas = pd.DataFrame(
        {
            "fuente": "hospital",
            # Cada equipo es un refrigerador físico distinto: su propio entorno.
            "entorno_id": "hospital_" + serie[ok],
            "serie_id": serie[ok],
            "timestamp": ts[ok],
            "temperatura_interna": temperatura[ok].to_numpy(dtype=float),
            "puerta_abierta": puerta[ok].to_numpy(dtype=float),
        }
    ).reset_index(drop=True)
    informe.filas_validas = int(lecturas["temperatura_interna"].notna().sum())
    tabla["timestamp"] = ts
    tabla["temperatura_interna"] = temperatura
    tabla["temp_aux"] = pd.to_numeric(tabla["temp_aux_sin_etiqueta"].replace("", np.nan), errors="coerce")
    tabla["humedad"] = pd.to_numeric(tabla["humidity"].replace("", np.nan), errors="coerce")
    return lecturas, informe, tabla


# ── ColdCare (Experimento D: exportación de thermal_readings) ────


def importar_coldcare_csv(ruta_csv: Path) -> tuple[pd.DataFrame, InformeImportacion]:
    """CSV exportado de `thermal_readings` (ver docs/ml/09_REPRODUCIBILITY.md):
    device_id, timestamp (ISO 8601 con zona), temperatura_interna y,
    opcional, apertura_refrigerador. Las lecturas manuales y simuladas deben
    excluirse en la consulta de exportación (origen = 'dispositivo')."""
    informe = InformeImportacion(fuente="coldcare")
    texto = _leer_texto_acotado(ruta_csv)
    df = pd.read_csv(io.StringIO(texto), sep=detectar_separador(texto), dtype=str)
    requeridas = {"device_id", "timestamp", "temperatura_interna"}
    if not requeridas <= set(df.columns):
        raise ErrorEsquemaDatos(f"{ruta_csv.name}: faltan columnas {sorted(requeridas - set(df.columns))}")
    informe.filas_leidas = len(df)
    informe.archivos[ruta_csv.name] = len(df)
    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce", format="ISO8601")
    temperatura = pd.to_numeric(df["temperatura_interna"], errors="coerce")
    informe.descartar("timestamp_invalido", int(ts.isna().sum()))
    puerta = (
        df["apertura_refrigerador"].str.strip().str.lower().map({"true": 1.0, "false": 0.0, "1": 1.0, "0": 0.0})
        if "apertura_refrigerador" in df.columns
        else pd.Series(np.nan, index=df.index)
    )
    ok = ts.notna()
    lecturas = pd.DataFrame(
        {
            "fuente": "coldcare",
            "entorno_id": "coldcare_" + df.loc[ok, "device_id"].str.strip(),
            "serie_id": df.loc[ok, "device_id"].str.strip(),
            "timestamp": ts[ok],
            "temperatura_interna": temperatura[ok].to_numpy(dtype=float),
            "puerta_abierta": puerta[ok].to_numpy(dtype=float),
        }
    ).reset_index(drop=True)
    informe.filas_validas = int(lecturas["temperatura_interna"].notna().sum())
    return lecturas, informe

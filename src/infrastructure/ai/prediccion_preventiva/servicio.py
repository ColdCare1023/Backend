"""Servicio de inferencia del modelo preventivo temporal.

Precedencia (docs/ml/06_BACKEND_INTEGRATION.md), evaluada por lectura:

1. Validez del dato: sin temperatura interna válida → SENSOR_FAULT.
2. Excursión actual (regla determinista 2-8 °C) → EXCURSION_EN_CURSO: el
   clasificador preventivo no se ejecuta; la alerta crítica la genera el
   flujo existente sin depender de este servicio.
3. Modelo cargado, verificado y compatible → si no, MODELO_NO_DISPONIBLE.
   En modo activo, además, la versión y el SHA-256 configurados deben ser los
   de la versión ACTIVA del registro de la base (HU-16/HU-46): el modelo
   preventivo se gobierna desde ese registro, no solo por variables de entorno.
4. Historial suficiente (ventana de 15 min con cobertura ≥ 80 %, reloj
   sincronizado y, si el conjunto lo usa, el SHT31 en el punto actual) → si
   no, HISTORIAL_INSUFICIENTE con su motivo.
5. Features con el MISMO extractor del entrenamiento e inferencia.

Nunca lanza: cualquier fallo se convierte en un estado explícito, para que
la ingesta de telemetría siga funcionando aunque el modelo no cargue.
"""

import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import NamedTuple

import numpy as np
import pandas as pd
import sklearn

from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.entities.prediccion_preventiva import PrediccionPreventiva
from src.domain.value_objects.motivo_bloqueo import MotivoBloqueo
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.ai.prediccion_preventiva import calidad, registro
from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    CALIDAD_EXCURSION_EN_CURSO,
    CALIDAD_FALLO_INFERENCIA,
    CALIDAD_HISTORIAL_INSUFICIENTE,
    CALIDAD_MODELO_NO_DISPONIBLE,
    CALIDAD_OK,
    CALIDAD_SENSOR_FAULT,
    CONFIGURACION_POR_DEFECTO,
    ESTADO_APROBADO,
    ESTADO_EXPERIMENTAL,
    ESTADO_VALIDADO_OFFLINE,
    configuracion_desde_manifiesto,
)
from src.infrastructure.ai.prediccion_preventiva.features import (
    CANAL_AMBIENTE,
    CANAL_HUMEDAD,
    CANAL_PUERTA,
    CANALES_POR_CONJUNTO,
    alcance_historial_s,
    evaluar_en_instante,
    nombres_features,
)

logger = logging.getLogger("infrastructure.ai.prediccion_preventiva.servicio")

MODO_DESACTIVADO = "desactivado"
MODO_SOMBRA = "sombra"
MODO_ACTIVO = "activo"
MODOS = (MODO_DESACTIVADO, MODO_SOMBRA, MODO_ACTIVO)

# En sombra el modelo no decide nada: se admite uno experimental para
# acumular evidencia prospectiva (Experimento D). En activo SOLO uno aprobado.
ESTADOS_ADMITIDOS = {
    MODO_SOMBRA: frozenset({ESTADO_EXPERIMENTAL, ESTADO_VALIDADO_OFFLINE, ESTADO_APROBADO}),
    MODO_ACTIVO: frozenset({ESTADO_APROBADO}),
}
TIEMPO_NO_SINCRONIZADO = "unsynced"

# Código estable de por qué el modelo preventivo no está disponible
# (/health/detalle y GET /api/ia/preventivo), junto al texto del motivo.
CODIGO_MODO_DESACTIVADO = "modo_desactivado"
CODIGO_VERSION_NO_CONFIGURADA = "version_no_configurada"
CODIGO_SHA256_NO_CONFIGURADO = "sha256_no_configurado"
CODIGO_SIN_VERSION_ACTIVA = "sin_version_activa_registrada"
CODIGO_VERSION_NO_ES_LA_ACTIVA = "version_distinta_de_la_activa"
CODIGO_HASH_NO_ES_EL_REGISTRADO = "hash_distinto_del_registrado"
CODIGO_ARTEFACTO_NO_VERIFICADO = "artefacto_no_verificado"
CODIGO_ESTADO_NO_ADMITIDO = "estado_no_admitido"
CODIGO_INCOMPATIBLE = "incompatible"
CODIGO_ERROR_CARGA = "error_de_carga"

# HU-46 esc. 3: estos códigos son un RECHAZO del artefacto (no es el aprobado,
# fue alterado o no es compatible) y se registran como error crítico. Los
# demás describen la configuración (modo desactivado, variable ausente o
# ninguna versión activa en la base, p. ej. tras desactivarla) y son avisos.
CODIGOS_RECHAZO = frozenset({
    CODIGO_VERSION_NO_ES_LA_ACTIVA, CODIGO_HASH_NO_ES_EL_REGISTRADO, CODIGO_ARTEFACTO_NO_VERIFICADO,
    CODIGO_ESTADO_NO_ADMITIDO, CODIGO_INCOMPATIBLE, CODIGO_ERROR_CARGA,
})

def es_rechazo_critico(modo: str | None, codigo: str | None) -> bool:
    """HU-46 esc. 3: único criterio del nivel CRITICAL para el preventivo, en el
    servicio y en la auditoría de arranque. Es crítico el RECHAZO del artefacto
    (CODIGOS_RECHAZO) cuando el modelo decide el riesgo (modo activo)."""
    return modo == MODO_ACTIVO and codigo in CODIGOS_RECHAZO


# Textos de motivo de activación (HU-46): el mismo del modelo legado cuando
# el artefacto desplegado no es el registrado.
MOTIVO_ARTEFACTO_DISTINTO = "el artefacto desplegado no coincide con el hash registrado"

# Códigos estables de los motivos que bloquean POST /activar (409, campo
# `codigos`). Los cuatro últimos son IA-B05 variantes 6 y 7: la versión que
# digita el admin debe ser la del manifiesto desplegado, y el manifiesto debe
# ser el que anotó la CLI en historial.json (no uno editado a mano).
ACTIVACION_VERSION_NO_CONFIGURADA = "version_preventiva_no_configurada"
ACTIVACION_VERSION_NO_DESPLEGADA = "version_distinta_de_la_desplegada"
ACTIVACION_SHA256_NO_CONFIGURADO = "sha256_no_configurado"
ACTIVACION_SHA256_DISTINTO = "sha256_distinto_del_registrado"
ACTIVACION_ARTEFACTO_DISTINTO = "artefacto_distinto_del_registrado"
ACTIVACION_MANIFIESTO_ILEGIBLE = "manifiesto_ilegible"
ACTIVACION_MANIFIESTO_NO_APROBADO = "manifiesto_no_aprobado"
ACTIVACION_MANIFIESTO_DISTINTO_DEL_HISTORIAL = "manifiesto_distinto_del_historial"
ACTIVACION_HORIZONTE_DISTINTO = "horizonte_distinto_del_manifiesto"
ACTIVACION_ESQUEMA_DISTINTO = "esquema_features_distinto_del_manifiesto"
ACTIVACION_SKLEARN_DISTINTO_DEL_MANIFIESTO = "sklearn_distinto_del_manifiesto"
ACTIVACION_SKLEARN_INCOMPATIBLE = "sklearn_incompatible_con_el_instalado"
# HU-17: el servicio no sabe calcular las features del manifiesto (conjunto
# sin canales servidos, lista de features distinta de la del extractor o
# configuración ilegible). Al cargar, el mismo caso es CODIGO_INCOMPATIBLE.
ACTIVACION_ESQUEMA_NO_SERVIBLE = "esquema_features_no_servible"

# Señales que `predecir` sabe construir a partir de LecturaTermica. Un conjunto
# cuyos canales (features.CANALES_POR_CONJUNTO) no estén aquí no se sirve.
CANALES_SERVIDOS = frozenset({CANAL_PUERTA, CANAL_AMBIENTE, CANAL_HUMEDAD})


def motivo_esquema_no_servible(manifiesto: dict) -> str | None:
    """HU-17: por qué el servicio NO puede calcular las features del
    manifiesto, o None si puede. Lo usan la carga y /activar, para que un
    conjunto que solo existe en el entrenamiento no cargue y deje toda
    lectura sin clasificar."""
    conjunto = manifiesto.get("conjunto_features")
    canales = CANALES_POR_CONJUNTO.get(conjunto) if isinstance(conjunto, str) else None
    if canales is None:
        return f"el servicio no calcula el conjunto de features {conjunto!r}"
    if not set(canales) <= CANALES_SERVIDOS:
        return f"el servicio no recibe las señales {sorted(set(canales) - CANALES_SERVIDOS)} del conjunto {conjunto}"
    try:
        esperadas = nombres_features(configuracion_desde_manifiesto(manifiesto), conjunto)
    except (KeyError, TypeError, ValueError) as exc:
        return f"la configuración del manifiesto no permite calcular las features ({type(exc).__name__})"
    if manifiesto.get("features") != esperadas:
        return f"las features del manifiesto no son las que el extractor calcula para el conjunto {conjunto}"
    return None


class VersionActivaRegistrada(NamedTuple):
    """Versión activa del registro de la base (ai_model_versions)."""

    version: str
    model_hash: str


class _NoDisponible(RuntimeError):
    def __init__(self, codigo: str, mensaje: str) -> None:
        super().__init__(mensaje)
        self.codigo = codigo


_EPOCA = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _a_ns(ts: datetime) -> int:
    """Nanosegundos UTC con aritmética entera (sin redondeo de float).
    SQLite devuelve datetimes naive: se asumen UTC, como en el resto del backend."""
    ts = ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
    return (ts - _EPOCA) // timedelta(microseconds=1) * 1000


def _version_menor(v: str) -> tuple[str, ...]:
    return tuple(v.split(".")[:2])


def _valor(x) -> float:
    """Señal de una lectura como float; NaN si falta o no es un número finito
    (la celda vacía de la exportación que lee el entrenamiento)."""
    if x is None or not isinstance(x, (int, float)):
        return math.nan
    valor = float(x)
    return valor if math.isfinite(valor) else math.nan


# Columnas de `calidad.limpiar` (y de la rejilla) para cada señal de la lectura.
_SENAL_DE_CANAL = {
    CANAL_PUERTA: ("puerta", "apertura_refrigerador"),
    CANAL_AMBIENTE: ("ambiente", "temperatura_ambiental"),
    CANAL_HUMEDAD: ("humedad", "humedad_ambiental"),
}


def _orden_de_exportacion(h: LecturaTermica) -> tuple:
    """Orden de `scripts/exportar_corridas.py` (hora de captura, boot_id,
    seq_no; NULL al final, como en PostgreSQL). Decide cuál de dos duplicados
    exactos conserva `calidad.limpiar` (el primero)."""
    return (_a_ns(h.timestamp), h.boot_id is None, h.boot_id or 0, h.seq_no is None, h.seq_no or 0)


def _senales_limpias(filas: list[LecturaTermica], canales: tuple[str, ...]) -> dict[str, np.ndarray]:
    """HU-17: las señales de `filas` tras la MISMA limpieza que el
    entrenamiento (`calidad.limpiar` sobre la exportación de corridas): un
    duplicado exacto (misma hora y misma temperatura interna) cuenta una vez,
    y una hora con temperaturas distintas se descarta entera (no hay forma de
    saber cuál es la verdadera). Sin esto, el servicio promediaba ambas.
    Devuelve `ts` (ns), `temperatura` y una entrada por canal del conjunto."""
    ordenadas = sorted(filas, key=_orden_de_exportacion)
    tabla = {
        "serie_id": [0] * len(ordenadas),
        "timestamp": np.array([_a_ns(h.timestamp) for h in ordenadas], dtype=np.int64),
        "temperatura_interna": np.array([_valor(h.temperatura_interna) for h in ordenadas], dtype=float),
    }
    for canal in canales:
        columna, atributo = _SENAL_DE_CANAL[canal]
        tabla[columna] = np.array([_valor(getattr(h, atributo)) for h in ordenadas], dtype=float)
    limpio, _ = calidad.limpiar(pd.DataFrame(tabla))
    senales = {
        "ts": limpio["timestamp"].to_numpy(dtype=np.int64),
        "temperatura": limpio["temperatura_interna"].to_numpy(dtype=float),
    }
    for canal in canales:
        senales[canal] = limpio[_SENAL_DE_CANAL[canal][0]].to_numpy(dtype=float)
    return senales


class ServicioPrediccionPreventiva:
    def __init__(
        self,
        modo: str,
        version: str | None,
        sha256_esperado: str | None = None,
        raiz: Path = registro.RAIZ_REGISTRO,
        version_activa: VersionActivaRegistrada | tuple[str, str] | None = None,
    ) -> None:
        if modo not in MODOS:
            raise ValueError(f"Modo preventivo desconocido: {modo!r}")
        self.modo = modo
        self._version = version or None
        self._sha256 = sha256_esperado or None
        self._raiz = raiz
        self._modelo = None
        self._manifiesto: dict | None = None
        self._config = None
        self._motivo_no_disponible: str | None = None
        self._codigo_no_disponible: str | None = None
        self._intentado = False
        self._lock = Lock()
        # HU-46: versión activa del registro de la base. Sin ella (None), el
        # modo activo no carga ningún modelo: la aprobación no es solo el
        # manifiesto y las variables de entorno.
        self._version_activa = VersionActivaRegistrada(*version_activa) if version_activa else None

    @property
    def version_configurada(self) -> str | None:
        return self._version

    @property
    def version_activa_registrada(self) -> VersionActivaRegistrada | None:
        return self._version_activa

    def fijar_version_activa(self, version_activa: VersionActivaRegistrada | tuple[str, str] | None) -> None:
        """HU-46/HU-16: fija la versión activa del registro (al arrancar y tras
        cada activación) y fuerza a reverificar el artefacto antes de volver a
        deserializarlo."""
        with self._lock:
            self._version_activa = VersionActivaRegistrada(*version_activa) if version_activa else None
            self._modelo = None
            self._manifiesto = None
            self._config = None
            self._motivo_no_disponible = None
            self._codigo_no_disponible = None
            self._intentado = False

    def _ruta_artefacto(self, version: str) -> Path | None:
        try:
            ruta = registro.directorio_version(version, self._raiz) / registro.ARCHIVO_MODELO
        except registro.ErrorRegistro:
            return None
        return ruta if ruta.exists() else None

    def hash_artefacto(self, version: str | None = None) -> str | None:
        """SHA-256 del archivo desplegado de `version` (por omisión, la de
        IA_PREVENTIVO_VERSION), o None si no existe."""
        objetivo = version or self._version
        ruta = self._ruta_artefacto(objetivo) if objetivo else None
        return registro.sha256_archivo(ruta) if ruta is not None else None

    def motivos_activacion(
        self,
        version: str,
        model_hash: str,
        horizonte_min: int | None = None,
        scikit_learn_version: str | None = None,
        feature_schema_version: str | None = None,
    ) -> list[MotivoBloqueo]:
        """HU-16 esc. 4 / HU-46: qué impide activar `version` como la que
        clasifica con IA_PREVENTIVO_MODO=activo. Se valida el artefacto que
        realmente se cargará (el de IA_PREVENTIVO_VERSION), no el legado.

        `horizonte_min`, `scikit_learn_version` y `feature_schema_version` son
        los que el admin registró en la base (IA-B05 v6): deben ser los del
        manifiesto desplegado (`configuracion.horizonte_min`,
        `versiones.scikit_learn` y `conjunto_features`), que es lo que de
        verdad se carga y lo que muestra la evidencia de HU-47 / HU-18 esc. 4,
        y la versión de scikit-learn debe poder cargarse con la instalada
        (misma regla que la carga). None = no se compara (llamadas que solo
        validan el artefacto)."""
        motivos: list[MotivoBloqueo] = []
        if self._version is None:
            motivos.append(MotivoBloqueo(ACTIVACION_VERSION_NO_CONFIGURADA, "IA_PREVENTIVO_VERSION no está configurada"))
        elif version != self._version:
            motivos.append(MotivoBloqueo(
                ACTIVACION_VERSION_NO_DESPLEGADA,
                f"la versión no es la desplegada en IA_PREVENTIVO_VERSION ({self._version})",
            ))
        if self._sha256 is None:
            motivos.append(MotivoBloqueo(ACTIVACION_SHA256_NO_CONFIGURADO, "IA_PREVENTIVO_SHA256 no está configurada"))
        elif model_hash != self._sha256:
            motivos.append(MotivoBloqueo(
                ACTIVACION_SHA256_DISTINTO, "IA_PREVENTIVO_SHA256 no coincide con el hash registrado"
            ))
        if self.hash_artefacto(version) != model_hash:
            motivos.append(MotivoBloqueo(ACTIVACION_ARTEFACTO_DISTINTO, MOTIVO_ARTEFACTO_DISTINTO))
            return motivos
        try:
            manifiesto = registro.leer_manifiesto(version, self._raiz)
            sha_manifiesto = registro.sha256_archivo(registro.ruta_manifiesto(version, self._raiz))
        except (registro.ErrorRegistro, OSError, ValueError):
            motivos.append(MotivoBloqueo(
                ACTIVACION_MANIFIESTO_ILEGIBLE, "el manifiesto del modelo preventivo no se pudo leer"
            ))
            return motivos
        # IA-B05 v7: el estado del manifiesto solo cuenta si lo escribió la CLI
        # (registrar/estado), que anota su SHA-256 en historial.json. Uno
        # editado a mano a APPROVED (sin pasar por la aprobación) no coincide.
        if sha_manifiesto != registro.sha256_manifiesto_en_historial(version, self._raiz):
            motivos.append(MotivoBloqueo(
                ACTIVACION_MANIFIESTO_DISTINTO_DEL_HISTORIAL,
                "el SHA-256 del manifiesto no coincide con el último anotado en historial.json "
                "(el manifiesto se modificó fuera de la CLI de registro)",
            ))
        if manifiesto.get("estado") not in ESTADOS_ADMITIDOS[MODO_ACTIVO]:
            motivos.append(MotivoBloqueo(
                ACTIVACION_MANIFIESTO_NO_APROBADO,
                f"el manifiesto está en estado {manifiesto.get('estado')}; activar exige {ESTADO_APROBADO}",
            ))
        # IA-B05 v6: la evidencia HU-47 toma H del registro; debe ser la del modelo cargado.
        horizonte_manifiesto = (manifiesto.get("configuracion") or {}).get("horizonte_min")
        if horizonte_min is not None and horizonte_min != horizonte_manifiesto:
            motivos.append(MotivoBloqueo(
                ACTIVACION_HORIZONTE_DISTINTO,
                f"el horizonte registrado ({horizonte_min} min) no es el del manifiesto desplegado "
                f"({horizonte_manifiesto} min)",
            ))
        # El esquema de features de la evidencia es el que digitó el admin: debe
        # ser el conjunto con el que se construyen las features del modelo.
        esquema_manifiesto = manifiesto.get("conjunto_features")
        if feature_schema_version is not None and feature_schema_version != esquema_manifiesto:
            motivos.append(MotivoBloqueo(
                ACTIVACION_ESQUEMA_DISTINTO,
                f"el esquema de features registrado ({feature_schema_version}) no es el del manifiesto "
                f"({esquema_manifiesto})",
            ))
        # HU-17: un esquema que el servicio no sabe calcular cargaría (o no) y
        # dejaría toda lectura sin clasificación IA: no se activa.
        if motivo_esquema_no_servible(manifiesto) is not None:
            motivos.append(MotivoBloqueo(
                ACTIVACION_ESQUEMA_NO_SERVIBLE,
                f"el servicio de inferencia no puede calcular las features del esquema del manifiesto "
                f"({esquema_manifiesto})",
            ))
        sklearn_manifiesto = (manifiesto.get("versiones") or {}).get("scikit_learn")
        if scikit_learn_version is not None and scikit_learn_version != sklearn_manifiesto:
            motivos.append(MotivoBloqueo(
                ACTIVACION_SKLEARN_DISTINTO_DEL_MANIFIESTO,
                f"la versión de scikit-learn registrada ({scikit_learn_version}) no es la del manifiesto "
                f"({sklearn_manifiesto})",
            ))
        if not isinstance(sklearn_manifiesto, str) or _version_menor(sklearn_manifiesto) != _version_menor(
            sklearn.__version__
        ):
            motivos.append(MotivoBloqueo(
                ACTIVACION_SKLEARN_INCOMPATIBLE,
                f"el modelo se entrenó con scikit-learn {sklearn_manifiesto} y el instalado es "
                f"{sklearn.__version__}; no cargaría",
            ))
        return motivos

    @property
    def habilitado(self) -> bool:
        return self.modo != MODO_DESACTIVADO

    def alcance_historial(self) -> timedelta:
        """HU-17: cuánto historial anterior a t necesita `predecir` para
        reproducir el vector del entrenamiento (`features.alcance_historial_s`
        con la configuración del manifiesto cargado; 1170 s por defecto). El
        caso de uso lo pide al repositorio por tiempo: con un tope fijo de
        filas, un nodo que reporta cada 5-9 s dejaba fuera el inicio de la
        ventana o de la segmentación. Puede cargar el modelo (llamar en un hilo)."""
        self._cargar()
        return timedelta(seconds=alcance_historial_s(self._config or CONFIGURACION_POR_DEFECTO))

    def _cargar(self) -> None:
        if self._intentado:
            return
        with self._lock:
            if self._intentado:
                return
            self._intentado = True
            try:
                self._modelo, self._manifiesto, self._config = self._cargar_y_validar()
            except Exception as exc:  # nunca propagar: la telemetría no depende del modelo
                self._motivo_no_disponible = str(exc)[:200]
                self._codigo_no_disponible = (
                    exc.codigo if isinstance(exc, _NoDisponible)
                    else CODIGO_ARTEFACTO_NO_VERIFICADO if isinstance(exc, registro.ErrorRegistro)
                    else CODIGO_ERROR_CARGA
                )
                # HU-46 esc. 3: el rechazo del artefacto (alterado, otro hash u
                # otra versión, no aprobado, incompatible) del modelo que decide
                # (modo activo) es un error crítico; el arranque además lo deja
                # en la bitácora (MODELO_RECHAZADO). La configuración pendiente
                # (ninguna versión activa, p. ej. tras /desactivar; variable
                # ausente; modo desactivado) y todo en sombra son avisos.
                nivel = logging.CRITICAL if es_rechazo_critico(self.modo, self._codigo_no_disponible) else logging.WARNING
                logger.log(
                    nivel, "Modelo preventivo no disponible (%s, %s): %s",
                    self.modo, self._codigo_no_disponible, self._motivo_no_disponible,
                )

    def _exigir_version_activa_registrada(self) -> None:
        """HU-46 esc. 3 / HU-16 esc. 1: en modo activo solo se carga la versión
        ACTIVA y aprobada del registro de la base, y solo si IA_PREVENTIVO_VERSION
        e IA_PREVENTIVO_SHA256 son exactamente los suyos. Se comprueba ANTES de
        leer el artefacto."""
        if self._sha256 is None:
            raise _NoDisponible(CODIGO_SHA256_NO_CONFIGURADO, "IA_PREVENTIVO_SHA256 no está configurada")
        activa = self._version_activa
        if activa is None:
            raise _NoDisponible(
                CODIGO_SIN_VERSION_ACTIVA,
                "No hay una versión activa en el registro de modelos de la base; el modo activo no carga "
                "ningún modelo preventivo",
            )
        if activa.version != self._version:
            raise _NoDisponible(
                CODIGO_VERSION_NO_ES_LA_ACTIVA,
                f"IA_PREVENTIVO_VERSION={self._version} no es la versión activa registrada ({activa.version})",
            )
        if activa.model_hash != self._sha256:
            raise _NoDisponible(
                CODIGO_HASH_NO_ES_EL_REGISTRADO,
                "IA_PREVENTIVO_SHA256 no coincide con el hash de la versión activa registrada",
            )

    def _cargar_y_validar(self):
        if not self.habilitado:
            raise _NoDisponible(CODIGO_MODO_DESACTIVADO, "modo_desactivado")
        if self._version is None:
            raise _NoDisponible(CODIGO_VERSION_NO_CONFIGURADA, "IA_PREVENTIVO_VERSION no está configurada")
        if self.modo == MODO_ACTIVO:
            self._exigir_version_activa_registrada()
        modelo, manifiesto = registro.cargar_verificado(self._version, self._raiz, self._sha256)
        if manifiesto.get("version") != self._version:
            raise _NoDisponible(CODIGO_ARTEFACTO_NO_VERIFICADO, "El manifiesto declara otra versión")
        if manifiesto["estado"] not in ESTADOS_ADMITIDOS[self.modo]:
            raise _NoDisponible(
                CODIGO_ESTADO_NO_ADMITIDO,
                f"La versión {self._version} está en estado {manifiesto['estado']}; el modo {self.modo} "
                f"exige {sorted(ESTADOS_ADMITIDOS[self.modo])}",
            )
        entrenado_con = manifiesto["versiones"]["scikit_learn"]
        if _version_menor(entrenado_con) != _version_menor(sklearn.__version__):
            raise _NoDisponible(
                CODIGO_INCOMPATIBLE, f"Modelo entrenado con scikit-learn {entrenado_con}; instalado {sklearn.__version__}"
            )
        # HU-17: solo carga un esquema de features que el servicio sabe
        # calcular (conjunto con canales servidos y la lista del extractor).
        no_servible = motivo_esquema_no_servible(manifiesto)
        if no_servible is not None:
            raise _NoDisponible(CODIGO_INCOMPATIBLE, f"{ACTIVACION_ESQUEMA_NO_SERVIBLE}: {no_servible}")
        config = configuracion_desde_manifiesto(manifiesto)
        if getattr(modelo, "n_features_in_", None) != len(manifiesto["features"]):
            raise _NoDisponible(CODIGO_INCOMPATIBLE, "Las features del modelo no coinciden con las del extractor")
        if [int(c) for c in getattr(modelo, "classes_", [])] != [0, 1]:
            raise _NoDisponible(
                CODIGO_INCOMPATIBLE, "El modelo no es el clasificador binario normal(0)/riesgo_preventivo(1)"
            )
        return modelo, manifiesto, config

    def motivo_rechazo(self) -> str | None:
        """Por qué el modelo habilitado no se cargó (hash que no coincide,
        estado no admitido...), o None si cargó o el modo es desactivado."""
        if not self.habilitado:
            return None
        self._cargar()
        return None if self._modelo is not None else self._motivo_no_disponible

    def motivo_codigo(self) -> str | None:
        """Código estable del motivo (CODIGO_*), o None si el modelo cargó."""
        if not self.habilitado:
            return CODIGO_MODO_DESACTIVADO
        self._cargar()
        return None if self._modelo is not None else self._codigo_no_disponible

    def es_version_activa_registrada(self) -> bool:
        """True si el modelo cargado es la versión activa y aprobada del
        registro de la base (en modo activo, la única que puede cargar)."""
        self._cargar()
        activa = self._version_activa
        return (
            self._modelo is not None
            and activa is not None
            and activa.version == self._version
            and activa.model_hash == (self._manifiesto or {}).get("artefacto", {}).get("sha256")
        )

    @property
    def disponible(self) -> bool:
        self._cargar()
        return self._modelo is not None

    def estado(self) -> dict:
        """Resumen para el endpoint de evidencia (sin rutas ni secretos)."""
        self._cargar()
        activa = self._version_activa
        registro_bd = {
            # HU-46: con qué versión del registro de la base se contrastó.
            "version_activa_registrada": activa.version if activa else None,
            "es_version_activa_registrada": self.es_version_activa_registrada(),
        }
        if self._modelo is None:
            return {
                "modo": self.modo, "disponible": False, "version": self._version,
                "motivo": self._motivo_no_disponible,
                "motivo_codigo": self.motivo_codigo(), **registro_bd,
            }
        m = self._manifiesto
        return {
            "modo": self.modo,
            "disponible": True,
            "motivo_codigo": None,
            **registro_bd,
            "version": m["version"],
            "estado_modelo": m["estado"],
            "motivo_estado": m.get("motivo_estado"),
            "sha256": m["artefacto"]["sha256"],
            "horizonte_min": m["configuracion"]["horizonte_min"],
            "conjunto_features": m["conjunto_features"],
            "features": m["features"],
            "umbral_decision": m["umbral_decision"],
            "puntuacion_calibrada": m.get("puntuacion_calibrada", False),
            "fuente_entrenamiento": m.get("fuente_entrenamiento"),
            "metricas_prueba": m.get("metricas_prueba"),
            "criterios_aceptacion": m.get("criterios_aceptacion"),
            "entrenado_en": m.get("entrenado_en"),
        }

    def _resultado(self, lectura: LecturaTermica, estado: str, motivo: str | None = None, **extra) -> PrediccionPreventiva:
        manifiesto = self._manifiesto or {}
        return PrediccionPreventiva(
            device_id=lectura.device_id,
            timestamp_inferencia=lectura.timestamp,
            modo=self.modo,
            estado=estado,
            motivo=motivo,
            horizonte_min=(manifiesto.get("configuracion") or {}).get("horizonte_min"),
            modelo_version=manifiesto.get("version", self._version),
            modelo_sha256=(manifiesto.get("artefacto") or {}).get("sha256"),
            puntuacion_calibrada=bool(manifiesto.get("puntuacion_calibrada", False)),
            **extra,
        )

    def predecir(self, lectura: LecturaTermica, historial: list[LecturaTermica]) -> PrediccionPreventiva:
        interna = lectura.temperatura_interna
        if interna is None or isinstance(interna, bool) or not math.isfinite(interna):
            return self._resultado(lectura, CALIDAD_SENSOR_FAULT, "sensor_interno_ausente_o_invalido")
        if lectura.es_excursion_confirmada():
            return self._resultado(lectura, CALIDAD_EXCURSION_EN_CURSO, "excursion_actual_regla_determinista")
        self._cargar()
        if self._modelo is None:
            return self._resultado(lectura, CALIDAD_MODELO_NO_DISPONIBLE, "modelo_no_cargado")
        if lectura.time_quality == TIEMPO_NO_SINCRONIZADO:
            return self._resultado(lectura, CALIDAD_HISTORIAL_INSUFICIENTE, "reloj_no_sincronizado")

        # Mismas filas que el entrenamiento (exportar_corridas + ingesta): solo
        # telemetría del nodo con hora sincronizada. Una lectura con el DS18B20
        # en falla no aporta temperatura (NaN), pero su puerta y su SHT31 sí,
        # igual que en `calidad.preparar_rejillas`. Y la misma limpieza de
        # duplicados (`calidad.limpiar`).
        previas = [h for h in historial if h.time_quality != TIEMPO_NO_SINCRONIZADO and not h.es_manual]
        conjunto = self._manifiesto["conjunto_features"]
        try:
            canales = CANALES_POR_CONJUNTO[conjunto]
            # Cada señal adicional solo se construye si el conjunto la usa.
            senales = _senales_limpias([*previas, lectura], canales)
            vector, estado, motivo = evaluar_en_instante(
                senales["ts"], senales["temperatura"], senales.get(CANAL_PUERTA), _a_ns(lectura.timestamp),
                self._config, conjunto, senales.get(CANAL_AMBIENTE), senales.get(CANAL_HUMEDAD),
            )
            if vector is None:
                return self._resultado(lectura, estado, motivo)
            puntuacion = float(self._modelo.predict_proba(vector.reshape(1, -1))[0, 1])
        except Exception as exc:  # defensa en profundidad, igual que RandomForestRiesgoService
            logger.exception("Fallo en la inferencia preventiva.")
            return self._resultado(lectura, CALIDAD_FALLO_INFERENCIA, f"excepcion:{type(exc).__name__}")
        umbral = float(self._manifiesto["umbral_decision"])
        return self._resultado(
            lectura,
            CALIDAD_OK,
            None,
            clase=NivelRiesgo.RIESGO_PREVENTIVO if puntuacion >= umbral else NivelRiesgo.NORMAL,
            puntuacion=round(puntuacion, 6),
            umbral=umbral,
            vector_features={n: round(float(v), 6) for n, v in zip(self._manifiesto["features"], vector, strict=True)},
        )


_instancia: ServicioPrediccionPreventiva | None = None


def obtener_servicio_preventivo(modo: str, version: str | None, sha256: str | None) -> ServicioPrediccionPreventiva:
    """Una instancia por proceso (el modelo se carga una sola vez). Si la
    configuración cambia, se recrea; la versión activa del registro se le
    vuelve a fijar al arrancar y en cada activación (ai/gobierno.py)."""
    global _instancia
    clave = (modo, version or None, sha256 or None)
    if _instancia is None or (_instancia.modo, _instancia._version, _instancia._sha256) != clave:
        _instancia = ServicioPrediccionPreventiva(modo, version, sha256)
    return _instancia

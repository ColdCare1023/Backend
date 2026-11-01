from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from src.domain.exceptions import DomainError
from src.domain.value_objects.estado_alerta import EstadoAlerta
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.domain.value_objects.rango_termico import RANGO_TERMICO_BPA


def _a_utc(valor: datetime) -> datetime:
    return valor if valor.tzinfo is not None else valor.replace(tzinfo=timezone.utc)


def _distancia_al_rango(temperatura: float) -> float:
    """Grados fuera de 2-8 °C (0 dentro del rango)."""
    if RANGO_TERMICO_BPA.contiene(temperatura):
        return 0.0
    return RANGO_TERMICO_BPA.distancia_al_limite(temperatura)


@dataclass(slots=True)
class AlertaTermica:
    """Alerta generada cuando una lectura clasifica como riesgo_preventivo o
    excursion_critica. Corrige AIV-02: representa un EPISODIO (no una lectura
    aislada) — mientras el riesgo se mantiene, la misma alerta se actualiza en
    vez de crearse una nueva por cada lectura ("tormenta de alertas")."""

    reading_id: UUID
    device_id: str
    nivel_riesgo: NivelRiesgo
    mensaje: str
    id: UUID | None = None
    revisada: bool = False
    revisada_por: UUID | None = None
    created_at: datetime | None = None
    # AIV-02 — control de episodio.
    episodio_abierto: bool = True
    lectura_inicial_id: UUID | None = None
    lectura_mas_reciente_id: UUID | None = None
    ultima_actualizacion: datetime | None = None
    cerrada_en: datetime | None = None
    # HU-23: máquina de estados PENDIENTE -> RECONOCIDA -> ATENDIDA. `revisada`/
    # `revisada_por` (arriba) se conservan en sincronía por compatibilidad con
    # lectores existentes (reportes PDF), pero `estado` es ahora la fuente de
    # verdad sobre el seguimiento operativo de la alerta.
    estado: EstadoAlerta = EstadoAlerta.PENDIENTE
    reconocida_en: datetime | None = None
    atendida_en: datetime | None = None
    # HU-53: el episodio ya fue avisado por correo (dedup persistente).
    notificada_en: datetime | None = None
    notificacion_intentos: int = 0
    # Evidencia de la excursión (migración 0035): inicio según la hora de CAPTURA
    # (las lecturas pueden llegar tarde), temperatura más alejada de 2-8 °C y si
    # el episodio se detectó con lecturas diferidas del buffer del nodo.
    iniciada_en: datetime | None = None
    temperatura_pico: float | None = None
    historica: bool = False

    def marcar_revisada(self, usuario_id: UUID) -> None:
        self.revisada = True
        self.revisada_por = usuario_id

    def reconocer(self, usuario_id: UUID, timestamp: datetime) -> None:
        """HU-23 Escenario 1: PENDIENTE -> RECONOCIDA. Solo válido desde
        PENDIENTE — reconocer dos veces (p. ej. dos pestañas del mismo
        farmacéutico) es un error de concurrencia, no un no-op silencioso."""
        if self.estado != EstadoAlerta.PENDIENTE:
            raise DomainError(
                f"La alerta ya fue reconocida (estado actual: {self.estado.value})"
            )
        self.estado = EstadoAlerta.RECONOCIDA
        self.reconocida_en = timestamp
        self.marcar_revisada(usuario_id)

    def marcar_atendida(self, timestamp: datetime) -> None:
        """HU-23 Escenario 2: RECONOCIDA -> ATENDIDA, al registrarse la
        acción correctiva (HU-27). Una alerta ya ATENDIDA no puede volver a
        atenderse — eso es exactamente la condición de carrera que HU-27
        Escenario 2 exige rechazar."""
        if self.estado == EstadoAlerta.ATENDIDA:
            raise DomainError("La alerta ya fue atendida por otra acción correctiva")
        self.estado = EstadoAlerta.ATENDIDA
        self.atendida_en = timestamp

    def registrar_lectura(self, reading_id: UUID, timestamp: datetime) -> None:
        """El episodio sigue activo: se actualiza la lectura más reciente y el
        timestamp, sin crear una fila nueva ni cambiar `reading_id` original
        (que sigue apuntando a la lectura que originó la alerta)."""
        self.lectura_mas_reciente_id = reading_id
        self.ultima_actualizacion = timestamp

    def actualizar_pico(self, temperatura: float | None) -> None:
        """Conserva la temperatura más alejada del rango 2-8 °C del episodio."""
        if temperatura is None:
            return
        if self.temperatura_pico is None or _distancia_al_rango(temperatura) > _distancia_al_rango(
            self.temperatura_pico
        ):
            self.temperatura_pico = temperatura

    @property
    def duracion_s(self) -> float | None:
        """Del inicio (hora de captura) al cierre, o a la última lectura si sigue abierto."""
        fin = self.cerrada_en or self.ultima_actualizacion
        if self.iniciada_en is None or fin is None:
            return None
        return max((_a_utc(fin) - _a_utc(self.iniciada_en)).total_seconds(), 0.0)

    def cerrar(self, timestamp: datetime) -> None:
        """El riesgo volvió a NORMAL (evento de recuperación) o el episodio
        escaló/desescaló a otro nivel de riesgo distinto."""
        self.episodio_abierto = False
        self.cerrada_en = timestamp

    def reabrir(self, reading_id: UUID, timestamp: datetime) -> None:
        """Dentro de la ventana de cooldown, un episodio recién cerrado del
        mismo dispositivo y tipo de riesgo se reabre en vez de crear una
        alerta nueva (evita flapping de apertura/cierre inmediato)."""
        self.episodio_abierto = True
        self.cerrada_en = None
        self.lectura_mas_reciente_id = reading_id
        self.ultima_actualizacion = timestamp

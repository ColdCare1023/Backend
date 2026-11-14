class DomainError(Exception):
    """Excepción base para violaciones de reglas de negocio del dominio."""


class LecturaInvalidaError(DomainError):
    """Se lanza cuando una lectura térmica no cumple validaciones básicas de rango."""


class DispositivoNoAutorizadoError(DomainError):
    """Se lanza cuando una lectura proviene de un dispositivo no registrado
    (principio de mínimo privilegio: solo dispositivos provisionados publican)."""


class CredencialRevocadaError(DispositivoNoAutorizadoError):
    """El dispositivo existe y sigue activo, pero su credencial MQTT fue revocada
    (HU-44). Con EMQX Serverless el usuario del broker se retira a mano, así que
    el backend no puede depender de que el broker ya rechace la conexión: deja de
    aceptar la evidencia del nodo aunque este todavía consiga publicar."""


class CredencialEnCuarentenaError(DispositivoNoAutorizadoError):
    """HU-44: rotación por compromiso con la consola del broker pendiente. El
    broker aún acepta el token filtrado y el backend no sabe con cuál se
    autenticó cada conexión, así que la telemetría no se acepta como evidencia.
    A diferencia de la revocación, el rechazo es TRANSITORIO: no lleva acuse y
    el nodo legítimo la conserva en LittleFS hasta que se confirme la consola."""


class CadenaTrazabilidadRotaError(DomainError):
    """Se lanza cuando la verificación de integridad de la cadena de hash falla."""


class CredencialesInvalidasError(DomainError):
    """Se lanza cuando la autenticación de un usuario falla."""


class CodigoVerificacionInvalidoError(CredencialesInvalidasError):
    """El código de verificación (o el desafío del login) no existe, caducó,
    ya se usó, se agotaron sus intentos o no coincide. `motivo` y `usuario_id`
    son para la bitácora; el mensaje al cliente es siempre genérico."""

    def __init__(self, mensaje: str, *, usuario_id: object = None, motivo: str | None = None) -> None:
        super().__init__(mensaje)
        self.usuario_id = usuario_id
        self.motivo = motivo


class ReenvioPrematuroError(DomainError):
    """Se pidió reenviar un código antes de que pase la espera mínima."""

    def __init__(self, segundos: int) -> None:
        super().__init__(f"Espera {segundos} segundos antes de pedir otro código.")
        self.segundos = segundos


class CodigosBloqueadosError(DomainError):
    """La cuenta superó el tope de intentos fallidos con códigos dentro de la
    ventana (CODIGO_MAX_FALLOS_CUENTA en CODIGO_VENTANA_FALLOS_HORAS): no se
    emiten ni se aceptan códigos de ese tipo hasta que pase."""

    def __init__(self, segundos: int) -> None:
        super().__init__("Se superó el número de intentos con códigos para esta cuenta. Intenta más tarde.")
        self.segundos = segundos


class PermisoDenegadoError(DomainError):
    """Se lanza cuando un usuario no tiene el rol requerido para una acción."""


class RecursoNoEncontradoError(DomainError):
    """Se lanza cuando un recurso solicitado no existe."""


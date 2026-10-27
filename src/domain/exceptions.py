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


class PermisoDenegadoError(DomainError):
    """Se lanza cuando un usuario no tiene el rol requerido para una acción."""


class RecursoNoEncontradoError(DomainError):
    """Se lanza cuando un recurso solicitado no existe."""


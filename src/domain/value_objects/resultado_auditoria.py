"""HU-42 esc. 1 / HU-50: resultado de un evento de la bitácora.

Cada operación sensible queda con su resultado: `exitoso`, `fallido` (el
intento no prosperó: credenciales, código, payload o validación), `denegado`
(el control de acceso lo rechazó: RBAC, sesión invalidada, credencial de
dispositivo revocada o suplantación) o `bloqueado` (un control temporal lo
retuvo: umbral de intentos fallidos, retención por compromiso).

Las filas nuevas guardan el resultado explícito. Las anteriores a la migración
0039 no se reescriben (audit_logs es de solo anexado): su resultado se deriva
de la acción con `resultado_de_accion`, el mismo criterio que usa el filtro SQL.

NOTIFICACION_FALLIDA y RESPALDO_FALLIDO (HU-52 esc. 2, HU-29 esc. 3) se
añadieron a FALLIDO después de 0039: las filas que ya se guardaron con
`resultado='exitoso'` entre 0039 y este cambio quedan tal cual (la tabla es de
solo anexado y el resultado está bajo el hash del eslabón); se distinguen por
la acción, que siempre dice que el envío o el respaldo falló.
"""

from enum import StrEnum


class ResultadoAuditoria(StrEnum):
    EXITOSO = "exitoso"
    FALLIDO = "fallido"
    DENEGADO = "denegado"
    BLOQUEADO = "bloqueado"


ACCIONES_POR_RESULTADO: dict[ResultadoAuditoria, frozenset[str]] = {
    ResultadoAuditoria.FALLIDO: frozenset(
        {
            "LOGIN_FALLIDO",
            "LOGIN_GOOGLE_FALLIDO",
            "LOGIN_CODIGO_FALLIDO",
            "RESTABLECER_CODIGO_FALLIDO",
            "PRUEBA_POSESION_FALLIDA",
            "LECTURA_RECHAZADA_ESQUEMA",
            "LECTURA_RECHAZADA_TIMESTAMP",
            "LECTURA_RECHAZADA_TAMANO",
            "LECTURA_SIMULADA_RECHAZADA",
            "LECTURA_CONFLICTO",
            "MODELO_RECHAZADO",
            "ACTIVAR_VERSION_MODELO_IA_RECHAZADO",
            # HU-44 esc. 3: la API del broker no ejecutó la operación (expulsión).
            "BROKER_OPERACION_FALLIDA",
            # HU-52 esc. 2: el proveedor de correo no aceptó el aviso.
            "NOTIFICACION_FALLIDA",
            # HU-29 esc. 3: la tarea programada de respaldo falló.
            "RESPALDO_FALLIDO",
        }
    ),
    ResultadoAuditoria.DENEGADO: frozenset(
        {
            "RBAC_DENEGADO",
            "SESION_RECHAZADA",
            "INTENTO_MODIFICAR_INFERENCIA",
            "DISPOSITIVO_RECHAZADO",
            "LECTURA_RECHAZADA_CREDENCIAL_REVOCADA",
            "LECTURA_RECHAZADA_SUPLANTACION",
            "EVENTO_RECHAZADO_SUPLANTACION",
            "EVENTO_CREDENCIAL_REVOCADA",
            "EVENTO_DISPOSITIVO_DADO_DE_BAJA",
            "EVENTO_DISPOSITIVO_DESCONOCIDO",
        }
    ),
    ResultadoAuditoria.BLOQUEADO: frozenset(
        {
            "UMBRAL_INTENTOS_FALLIDOS_EXCEDIDO",
            "LECTURA_RETENIDA_ROTACION_COMPROMISO",
            "EVENTO_RETENIDO_ROTACION_COMPROMISO",
            "RETENCION_INTENTOS_SUPRIMIDOS",
        }
    ),
}

ACCIONES_NO_EXITOSAS: frozenset[str] = frozenset().union(*ACCIONES_POR_RESULTADO.values())


def resultado_de_accion(accion: str) -> ResultadoAuditoria:
    """Resultado de una acción cuando no se indicó explícitamente."""
    for resultado, acciones in ACCIONES_POR_RESULTADO.items():
        if accion in acciones:
            return resultado
    return ResultadoAuditoria.EXITOSO

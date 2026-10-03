from enum import StrEnum


class EstadoAlerta(StrEnum):
    """HU-23: máquina de estados de acuse y seguimiento de alertas críticas.

    PENDIENTE -> RECONOCIDA -> ATENDIDA. Una alerta nace PENDIENTE; el
    farmacéutico la reconoce (queda constancia de quién y cuándo); mientras
    esté RECONOCIDA y no exista una acción correctiva registrada, sigue
    pendiente de atención; al registrarse la acción (HU-27) pasa a ATENDIDA.
    """

    PENDIENTE = "pendiente"
    RECONOCIDA = "reconocida"
    ATENDIDA = "atendida"

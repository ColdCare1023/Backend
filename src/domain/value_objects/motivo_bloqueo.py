"""HU-46 / HU-16 esc. 4: motivo por el que una versión del modelo no puede
activarse, con un código estable.

Es un `str` (el texto legible de siempre, el mismo que ya devolvían la lista
`motivos_bloqueo` y el `detail` del 409) que además lleva `codigo`, para que
un cliente distinga cada causa sin interpretar el texto. Como sigue siendo un
`str`, quien comparaba o unía los motivos como texto no cambia.
"""

CODIGO_OTRO = "otro"


class MotivoBloqueo(str):
    codigo: str

    def __new__(cls, codigo: str, texto: str) -> "MotivoBloqueo":
        motivo = super().__new__(cls, texto)
        motivo.codigo = codigo
        return motivo

    def __reduce__(self):
        return (MotivoBloqueo, (self.codigo, str(self)))


def codigo_de(motivo: str) -> str:
    """Código estable de un motivo (`otro` si es un texto sin código)."""
    return getattr(motivo, "codigo", CODIGO_OTRO)

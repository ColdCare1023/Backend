import time
from collections import OrderedDict, deque


class SlidingWindowRateLimiter:
    """Limitador de intentos en memoria con ventana deslizante por clave (IP).

    Solo cuenta intentos fallidos: un login correcto reinicia la clave, de modo
    que usuarios legítimos detrás de una IP compartida no quedan bloqueados.
    Al ser en memoria cubre un único proceso (suficiente para el prototipo);
    en un despliegue multi-instancia se sustituiría por un backend Redis.
    """

    def __init__(
        self, max_intentos: int, ventana_segundos: float, max_claves: int = 10_000
    ) -> None:
        if max_claves < 1:
            raise ValueError("max_claves debe ser al menos 1")
        self._max_intentos = max_intentos
        self._ventana = ventana_segundos
        self._max_claves = max_claves
        self._fallos: OrderedDict[str, deque[float]] = OrderedDict()

    def _depurar(self, clave: str, ahora: float) -> deque[float] | None:
        cola = self._fallos.get(clave)
        if cola is None:
            return None
        limite = ahora - self._ventana
        while cola and cola[0] <= limite:
            cola.popleft()
        if not cola:
            self._fallos.pop(clave, None)
            return None
        self._fallos.move_to_end(clave)
        return cola

    def bloqueado(self, clave: str) -> bool:
        cola = self._depurar(clave, time.monotonic())
        return cola is not None and len(cola) >= self._max_intentos

    def registrar_fallo(self, clave: str) -> None:
        ahora = time.monotonic()
        cola = self._depurar(clave, ahora)
        if cola is None:
            if len(self._fallos) >= self._max_claves:
                self._fallos.popitem(last=False)
            cola = deque()
            self._fallos[clave] = cola
        cola.append(ahora)

    def reiniciar(self, clave: str) -> None:
        self._fallos.pop(clave, None)

    def segundos_para_reintentar(self, clave: str) -> int:
        cola = self._depurar(clave, time.monotonic())
        if not cola:
            return 0
        restante = self._ventana - (time.monotonic() - cola[0])
        return max(1, int(restante) + 1)

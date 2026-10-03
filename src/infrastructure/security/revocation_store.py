import time
from collections import OrderedDict
from threading import Lock


class JtiStore:
    """Registro en memoria de identificadores JWT (jti) con expiración.

    Dos usos:
    - Revocación de access tokens: el logout registra el jti hasta su `exp`;
      cualquier request posterior con ese token se rechaza (OWASP ASVS 3.3).
    - Tickets SSE de un solo uso: el jti se consume al abrir el stream; un
      ticket reutilizado (p. ej. filtrado en logs de un proxy) ya no sirve.

    Al ser en memoria cubre un único proceso — suficiente para el prototipo;
    en despliegue multi-instancia se sustituiría por Redis con TTL.
    """

    def __init__(self, max_entradas: int = 10_000) -> None:
        if max_entradas < 1:
            raise ValueError("max_entradas debe ser al menos 1")
        self._max_entradas = max_entradas
        self._entradas: OrderedDict[str, float] = OrderedDict()
        self._lock = Lock()

    def _purgar(self, ahora: float) -> None:
        expirados = [jti for jti, exp in self._entradas.items() if exp <= ahora]
        for jti in expirados:
            del self._entradas[jti]

    def _registrar_acotado(self, jti: str, expira_en_ts: float) -> None:
        self._entradas.pop(jti, None)
        if len(self._entradas) >= self._max_entradas:
            self._entradas.popitem(last=False)
        self._entradas[jti] = expira_en_ts

    def registrar(self, jti: str, expira_en_ts: float) -> None:
        """Marca el jti como revocado/consumido hasta su timestamp de expiración."""
        ahora = time.time()
        with self._lock:
            self._purgar(ahora)
            self._registrar_acotado(jti, expira_en_ts)

    def contiene(self, jti: str) -> bool:
        ahora = time.time()
        with self._lock:
            self._purgar(ahora)
            if jti in self._entradas:
                self._entradas.move_to_end(jti)
                return True
            return False

    def consumir(self, jti: str, expira_en_ts: float) -> bool:
        """Uso único atómico: devuelve True si el jti no había sido usado
        (y lo registra); False si ya fue consumido."""
        ahora = time.time()
        with self._lock:
            self._purgar(ahora)
            if jti in self._entradas:
                return False
            self._registrar_acotado(jti, expira_en_ts)
            return True

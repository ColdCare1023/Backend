from src.infrastructure.security.rate_limiter import SlidingWindowRateLimiter


def test_limitador_no_crea_claves_al_consultar_ips_aleatorias():
    limiter = SlidingWindowRateLimiter(max_intentos=2, ventana_segundos=60, max_claves=2)
    assert limiter.bloqueado("ip-no-vista") is False
    assert len(limiter._fallos) == 0


def test_limitador_acota_claves_y_expulsa_la_mas_antigua():
    limiter = SlidingWindowRateLimiter(max_intentos=2, ventana_segundos=60, max_claves=2)
    limiter.registrar_fallo("ip-1")
    limiter.registrar_fallo("ip-2")
    limiter.registrar_fallo("ip-3")
    assert set(limiter._fallos) == {"ip-2", "ip-3"}

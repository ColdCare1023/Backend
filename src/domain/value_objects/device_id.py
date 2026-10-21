"""BE-017: identificador de dispositivo.

Es el usuario MQTT del nodo, un segmento de tópico y de ruta HTTP, y aparece en
reportes PDF y en la bitácora. Se acota al mismo alfabeto que el contrato v1.0
del firmware: sin `/`, `+`, `#`, `?`, espacios ni marcado.
"""

PATRON_DEVICE_ID = r"^[A-Za-z0-9_-]{1,50}$"

# Evidencia — HU-06: Buffer offline (LittleFS) — lado backend

Entorno: `infra-local/` (PostgreSQL 16, EMQX 5.8.6 con TLS 8883 y CA propia, backend FastAPI en Docker), nodo ESP32 DevKitC real con firmware ColdCare1023/IoT `develop` (v1.2.0) salvo donde se indique, frontend ColdCare1023/Frontend `main`. Fechas: 2–3 de octubre de 2026.

## Defectos corregidos
1. **Hora de lecturas capturadas sin NTP.** Tras un reinicio sin red, el nodo envía lecturas `unsynced`
   (sin hora real). Ahora llevan `uptime`; cuando llega la primera lectura sincronizada **del mismo
   arranque**, el backend reconstruye su hora en `timestamp_estimado` (migración 0016). `timestamp` no se
   sobrescribe (evidencia y cadena de hash intactas).
2. **Aviso al 80 %.** El evento `buffer_80` del firmware se audita como `BUFFER_CAPACIDAD_ALTA`.

## Verificación en vivo
Reinicio del nodo sin Wi-Fi y reconexión posterior (boot 19): 10 lecturas `unsynced` reancladas con
corrección uniforme y espaciado exacto de 30 s, empalmando con la primera sincronizada:
```
seq_no | declarado (sin NTP) | timestamp_estimado    | corrección
     8 | 02:50:34            | 02:58:05.997          | +452 s
    17 | 02:55:04            | 03:02:35.997          | +452 s
    18 | 03:03:06 (synced)   |                       |
```
Firmware ColdCare v1.2.0: la primera lectura del arranque (`unsynced`, `captured_at: null`) quedó reanclada
al inicio del arranque (03:30:09) al llegar la siguiente sincronizada.

Saturación de prueba (buffer de 10): `BUFFER_SATURADO` y `BUFFER_CAPACIDAD_ALTA` auditados al reconectar.

Pruebas: `test_lecturas_sin_ntp_se_reanclan_al_llegar_una_sincronizada`,
`test_lectura_unsynced_se_reancla_con_una_sincronizada_del_mismo_arranque`.
La evidencia de hardware (guardado sin red, recuperación tras reinicio, 80 %, saturación) está en
ColdCare1023/IoT `docs/evidencias/HU-06_evidencia.md`.

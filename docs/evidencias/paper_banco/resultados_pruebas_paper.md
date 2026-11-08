# Resultados de las pruebas de banco (generado)

Generado el 2026-11-08 18:07 con `scripts/paper_tabla.py` a partir de `Backend/infra-local/evidencia/paper/*/resumen.json`. No editar a mano: se regenera. Las cifras son media ± desviación estándar (mínimo–máximo) entre repeticiones.

## Tabla por escenario

| Métrica | BASE (N=5) | S12b (N=5) | S1 (N=1) | S2 (N=5) | S5 (N=5) | S6 (N=5) | S7 (N=5) | S9 (N=5) |
|---|---|---|---|---|---|---|---|---|
| Guardadas | 19.4 ± 0.5 (19–20) | 18.0 ± 0.0 (18–18) | 4321 | 90.6 ± 7.8 (79–99) | 69.2 ± 10.7 (59–83) | 9.0 ± 0.0 (9–9) | 195.4 ± 1.9 (192–197) | 42.0 ± 0.0 (42–42) |
| Faltantes | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) | 0 | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) |
| Duplicadas | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) | 0 | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) |
| Reenvíos en el broker | 5.0 ± 0.0 (5–5) | 0.0 ± 0.0 (0–0) | 32 | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) | 2.0 ± 0.0 (2–2) | 2.4 ± 1.3 (0–3) | 0.0 ± 0.0 (0–0) |
| Diferidas | 13.0 ± 0.0 (13–13) | 0.0 ± 0.0 (0–0) | 25 | 64.6 ± 3.0 (63–70) | 52.4 ± 10.4 (41–61) | 1.0 ± 0.0 (1–1) | 186.8 ± 1.6 (184–188) | 0.0 ± 0.0 (0–0) |
| Fuera de orden | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) | 0 | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) |
| boot_id distintos | 1.0 ± 0.0 (1–1) | 2.0 ± 0.0 (2–2) | 1 | 1.0 ± 0.0 (1–1) | 3.4 ± 3.1 (2–9) | 1.0 ± 0.0 (1–1) | 1.0 ± 0.0 (1–1) | 1.0 ± 0.0 (1–1) |
| Duración del corte (s) | 148.6 ± 0.1 (148.5–148.7) | — | 33.9 | 642.9 ± 3.0 (640.4–647.9) | 490.2 ± 88.9 (423–609.4) | 35.9 ± 0.2 (35.6–36.1) | 1852.0 ± 17.4 (1821.4–1862.5) | — |
| Vaciado (s) | 2.4 ± 0.1 (2.3–2.6) | — | 43.1 | 14.1 ± 1.2 (12.8–15.8) | 9.8 ± 2.2 (7.9–12.5) | 0.0 ± 0.0 (0–0) | 39.7 ± 0.9 (38.7–41) | — |
| Vaciado (registros/s) | 5.3 ± 0.2 (5.06–5.54) | — | 0.12 | 4.5 ± 0.4 (3.98–4.94) | 4.9 ± 0.2 (4.7–5.22) | 1000.0 ± 0.0 (1000–1000) | 4.7 ± 0.1 (4.56–4.86) | — |
| sin_datos: último reporte → detección real (s) | 43.4 ± 3.3 (39.1–46.7) | 47.5 ± 15.3 (30.1–59.8) | — | 49.3 ± 10.9 (31.2–59.9) | 42.4 ± 7.1 (35.3–53.3) | 35.8 ± 0.3 (35.4–36) | 51.1 ± 9.8 (34.3–57.9) | — |
| sin_datos: último reporte → `abierto_en` nominal (s) | 30.3 ± 0.1 (30.2–30.3) | 30.0 ± 0.0 (30–30) | — | 30.9 ± 0.1 (30.8–31) | 30.8 ± 0.5 (30.2–31.5) | 30.3 ± 0.0 (30.2–30.3) | 30.0 ± 0.2 (29.8–30.2) | — |
| Energía restituida → primera lectura confirmada (s) | — | 7.4 ± 1.3 (6.4–9.5) | — | — | — | — | — | — |
| Expuestas a la falla del COMMIT | 12.2 ± 0.4 (12–13) | — | — | — | — | 1.0 ± 0.0 (1–1) | — | — |
| Perdidas con el diseño (acuse de aplicación) | 0.0 ± 0.0 (0–0) | — | — | — | — | 0.0 ± 0.0 (0–0) | — | — |
| Pérdida estimada solo con PUBACK | 12.2 ± 0.4 (12–13) | — | — | — | — | 1.0 ± 0.0 (1–1) | — | — |
| Puerta: apertura → primera lectura abierta confirmada (s) | — | — | — | — | — | — | — | 7.8 ± 0.1 (7.8–7.9) |
| Puerta: apertura → estado prolongado (≥ 60 s) confirmado (s) | — | — | — | — | — | — | — | 67.9 ± 0.1 (67.8–67.9) |
| Puerta: cierre → primera lectura cerrada confirmada (s) | — | — | — | — | — | — | — | 7.8 ± 0.1 (7.8–7.9) |
| Puerta: duración medida por el nodo − duración marcada (s) | — | — | — | — | — | — | — | -3.8 ± 5.2 (-13–0) |
| Alertas abiertas en la corrida | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) | 0 | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) | 0.0 ± 0.0 (0–0) |
| Hielo: lecturas en la ventana estable (últimos 180 s en el baño) | — | — | — | — | — | — | — | — |
| Hielo: sesgo medio vs 0.0 °C (°C) | — | — | — | — | — | — | — | — |
| Hielo: error absoluto medio vs 0.0 °C (°C) | — | — | — | — | — | — | — | — |
| Hielo: error absoluto máximo (°C) | — | — | — | — | — | — | — | — |
| Hielo: desviación estándar en la ventana estable (°C) | — | — | — | — | — | — | — | — |
| Hielo: inmersión → primera captura con 90 % del escalón (s, resolución 10 s) | — | — | — | — | — | — | — | — |
| Excursión < 2 °C: recepción de la lectura → alerta visible (s) | — | — | — | — | — | — | — | — |
| Excursión < 2 °C: COMMIT de la lectura → alerta visible (s) | — | — | — | — | — | — | — | — |
| Latencia en línea, mediana (s) | 0.3 ± 0.0 (0.305–0.358) | 0.6 ± 0.4 (0.035–1.111) | 0.217 | 0.9 ± 0.1 (0.854–1.055) | 1.5 ± 1.8 (0.247–4.658) | 0.4 ± 0.0 (0.299–0.383) | 0.1 ± 0.1 (-0.062–0.312) | 0.8 ± 0.0 (0.803–0.874) |
| Latencia en línea, p95 (s) | 8.6 ± 0.3 (8.273–8.831) | 1.2 ± 0.4 (0.793–1.767) | 1.56 | 8.0 ± 4.7 (2.245–13.302) | 8.8 ± 5.6 (3.709–17.281) | 12.7 ± 0.3 (12.319–13.035) | 10.4 ± 2.9 (7.289–14.98) | 0.9 ± 0.0 (0.853–0.938) |

## Corridas

| Corrida | Guardadas | Faltantes | Duplicadas | Reenvíos | Diferidas | Fuera de orden | boots | Cortes | Corte (s) | Vaciado (s) | sin_datos real (s) | Lat. en línea mediana (s) | Sin hora |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BASE_r1_20261105-1933 | 20 | 0 | 0 | 5 | 13 | 0 | 1 | 1 | 148.6 | 2.4 | 40.7 | 0.358 | 0 |
| BASE_r2_20261105-1936 | 19 | 0 | 0 | 5 | 13 | 0 | 1 | 1 | 148.6 | 2.3 | 39.1 | 0.356 | 0 |
| BASE_r3_20261105-1940 | 19 | 0 | 0 | 5 | 13 | 0 | 1 | 1 | 148.5 | 2.6 | 46.7 | 0.331 | 0 |
| BASE_r4_20261105-1943 | 19 | 0 | 0 | 5 | 13 | 0 | 1 | 1 | 148.6 | 2.5 | 45.8 | 0.305 | 0 |
| BASE_r5_20261105-1946 | 20 | 0 | 0 | 5 | 13 | 0 | 1 | 1 | 148.7 | 2.4 | 44.6 | 0.34 | 0 |
| S12b_r1_20261105-2341 | 18 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | None | None | 57.1 | 0.744 | 0 |
| S12b_r2_20261105-2348 | 18 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | None | None | 59.8 | 1.111 | 0 |
| S12b_r3_20261105-2355 | 18 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | None | None | 30.1 | 0.035 | 0 |
| S12b_r4_20261106-0002 | 18 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | None | None | 59.0 | 0.928 | 0 |
| S12b_r5_20261106-0009 | 18 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | None | None | 31.6 | 0.304 | 0 |
| S1_r1_20261106-1010 | 4321 | 0 | 0 | 32 | 25 | 0 | 1 | 15 | 33.9 | 43.1 | None | 0.217 | 0 |
| S2_r1_20261105-1353 | 79 | 0 | 0 | 0 | 63 | 0 | 1 | 1 | 641.9 | 14.4 | 48.4 | 1.055 | 0 |
| S2_r2_20261105-1407 | 94 | 0 | 0 | 0 | 63 | 0 | 1 | 1 | 643.2 | 13.1 | 59.9 | 0.971 | 0 |
| S2_r3_20261105-1423 | 87 | 0 | 0 | 0 | 64 | 0 | 1 | 1 | 647.9 | 14.5 | 31.2 | 0.966 | 0 |
| S2_r4_20261105-1437 | 99 | 0 | 0 | 0 | 70 | 0 | 1 | 2 | 641.3 | 15.8 | 52.8 | 0.895 | 0 |
| S2_r5_20261105-1454 | 94 | 0 | 0 | 0 | 63 | 0 | 1 | 1 | 640.4 | 12.8 | 54.0 | 0.854 | 0 |
| S5_r1_20261105-1510 | 59 | 0 | 0 | 0 | 41 | 0 | 2 | 1 | 423.0 | 7.9 | 35.3 | 0.818 | 0 |
| S5_r2_20261105-1520 | 83 | 0 | 0 | 0 | 59 | 0 | 9 | 1 | 562.3 | 12.5 | 53.3 | 1.046 | 0 |
| S5_r3_20261105-1833 | 68 | 0 | 0 | 0 | 61 | 0 | 2 | 2 | 427.6 | 8.7 | 45.4 | 4.658 | 457 |
| S5_r4_20261105-1847 | 77 | 0 | 0 | 0 | 60 | 0 | 2 | 1 | 609.4 | 11.9 | 38.4 | 0.623 | 0 |
| S5_r5_20261105-1900 | 59 | 0 | 0 | 0 | 41 | 0 | 2 | 1 | 428.7 | 8.1 | 39.6 | 0.247 | 0 |
| S6_r1_20261105-1925 | 9 | 0 | 0 | 2 | 1 | 0 | 1 | 1 | 36.1 | 0.0 | 35.9 | 0.359 | 0 |
| S6_r2_20261105-1927 | 9 | 0 | 0 | 2 | 1 | 0 | 1 | 1 | 35.6 | 0.0 | 35.4 | 0.383 | 0 |
| S6_r3_20261105-1928 | 9 | 0 | 0 | 2 | 1 | 0 | 1 | 1 | 36.1 | 0.0 | 35.9 | 0.367 | 0 |
| S6_r4_20261105-1930 | 9 | 0 | 0 | 2 | 1 | 0 | 1 | 1 | 35.8 | 0.0 | 35.6 | 0.365 | 0 |
| S6_r5_20261105-1931 | 9 | 0 | 0 | 2 | 1 | 0 | 1 | 1 | 36.1 | 0.0 | 36.0 | 0.299 | 0 |
| S7_r1_20261105-1951 | 192 | 0 | 0 | 0 | 184 | 0 | 1 | 1 | 1821.4 | 39.1 | 54.6 | 0.312 | 0 |
| S7_r2_20261105-2023 | 196 | 0 | 0 | 3 | 187 | 0 | 1 | 1 | 1859.2 | 39.5 | 57.9 | 0.218 | 0 |
| S7_r3_20261105-2055 | 196 | 0 | 0 | 3 | 188 | 0 | 1 | 1 | 1862.5 | 40.3 | 51.1 | 0.123 | 0 |
| S7_r4_20261105-2128 | 197 | 0 | 0 | 3 | 187 | 0 | 1 | 1 | 1854.3 | 41.0 | 34.3 | 0.039 | 0 |
| S7_r5_20261105-2201 | 196 | 0 | 0 | 3 | 188 | 0 | 1 | 1 | 1862.5 | 38.7 | 57.6 | -0.062 | 0 |
| S9_r1_20261105-2244 | 42 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | None | None | None | 0.874 | 0 |
| S9_r2_20261105-2252 | 42 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | None | None | None | 0.849 | 0 |
| S9_r3_20261105-2300 | 42 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | None | None | None | 0.845 | 0 |
| S9_r4_20261105-2308 | 42 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | None | None | None | 0.803 | 0 |
| S9_r5_20261105-2316 | 42 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | None | None | None | 0.81 | 0 |

## Notas por corrida

- **S2 r1:** las marcas del operador no quedaron registradas. El corte se tomó de los datos
  (`corte_inferido`).
- **S2 r4:** además del corte provocado hubo un **corte espontáneo de 95 s** a las 19:38 UTC
  (7 diferidas, 0 perdidas). La tabla usa el corte provocado, que es el de más diferidas.
- **S2 r5:** el cierre automático se adelantó al vaciado por un error de la herramienta, ya
  corregido: `fin` ahora espera el vaciado. La base se reexportó después del vaciado y sus cifras son
  válidas. `mqtt.csv` **no** incluye los mensajes del vaciado, así que en esa repetición los
  reenvíos **no se midieron**. El 0 de la tabla no cuenta.
- **S5 r2:** reinicios en bucle por watchdog después del reset (ver H1). Por eso hay 8 `boot_id`.
- **S5 r3:**
  - **Inicio con cola heredada:** empezó mientras el nodo vaciaba la cola de un corte no provocado
    de unas 2 h.
  - **Lecturas sin hora:** las 457 llegadas sin hora de captura (`sin_hora.csv`) son de esa cola
    y quedan fuera del análisis (ver H2).
  - **Log serie:** hay log serie (`serial.log`), y en esta repetición no se repitió el bucle.
- **S7 (r1–r5):**
  - **Duración del corte:** el corte medido (≈ 1 855 s) supera los 30 min con el broker detenido
    (1 800 s). La diferencia es la espera de reconexión del nodo y el tiempo de arranque de EMQX.
  - **Métricas de EMQX:** no quedaron guardadas porque el script usó un comando equivocado
    (`emqx ctl metrics`; el correcto es `emqx ctl broker metrics`). No afecta: con el broker detenido
    no le llega ningún mensaje que pueda descartar.
  - **Reconexión del backend:** no hizo falta reiniciar el backend en ninguna repetición.
  - **Puerta:** las 5 repeticiones corrieron con los imanes separados (`puerta_abierta = t`). No
    afecta lo que mide S7.
  - **Latencia (r5):** la mediana en línea de r5 es −0,06 s. Es el desfase entre el reloj del nodo y el
    de la laptop, menor a 1 s.

- **S9 (r1–r5):**
  - **Protocolo:** con horario fijo, los imanes del MC-38 se separaron 5 min con la puerta del
    refrigerador cerrada, y el script puso las marcas en el mismo segundo.
  - **Correcciones de la operadora:** el cierre de r2 se corrigió a 03:58:30Z y el de r3 a
    04:06:50Z, según lo que informó la operadora.
  - **r2:** el nodo registra el cierre real hacia las 03:58:25 y una reapertura de 1 s al reacomodar
    el imán. El error de duración de r2 (−13 s) refleja esa maniobra y no cuenta.
  - **Ni alertas ni incidentes en las 5 repeticiones:** confirma que la puerta abierta solo cambia
    el estado del panel.

- **S12b (r1–r5):**
  - **Protocolo:** el nodo se alimentó con un cargador de pared de 5 V con interruptor. Se cortó la
    energía con el interruptor (el LED del ESP32 se apaga), en horario fijo y por 3 min.
  - **Incidente previo:** el `sin_datos` de las 04:35Z, antes de r1, fue al mover el nodo del USB al
    cargador. Queda fuera de las corridas.

- **S1 r1 (12 h, 6 nov. 10:10:59 → 22:11:06 hora de Lima):**
  - **Arranque:** la laptop había estado apagada (sin datos de 02:57 a 10:09) y el nodo reinició
    (boot_id 1061). El script esperó a que la sonda volviera a 2,5–7,5 °C sin alerta abierta: arrancó
    con 7,5 °C, que bajó a ~3,4 °C en pocos minutos.
  - **Red:** laptop en WinPina 5G (192.168.18.114) y nodo en WinPina 4G (misma red).
  - **Alimentación (corregido el 7 nov.):** el nodo iba al **puerto USB de la laptop**, con el mismo
    cable de siempre, según la operadora. La nota de `corrida.json` dice "nodo en cargador de pared"
    porque el script la escribió por defecto; queda como dato crudo, pero es incorrecta. La laptop
    estuvo despierta las 12 h (keep-awake) y el nodo no se reinició ni una vez. Ver H10.
  - **Resultado:** 4 321 guardadas (seq 23–4343), 0 faltantes, 0 duplicadas en la base y 0 fuera de orden.
    No se abrió ninguna alerta: las 4 321 lecturas fueron `normal`. DS18B20: 3,92 ± 0,33 °C (3,31–7,38).
  - **Cortes no provocados:** hubo 15, de 31 a 46 s cada uno, todos entre las 15:38 y las 17:57 hora
    de Lima, con 25 diferidas recuperadas sin pérdida. Ver H9.
  - **Broker:** 32 reenvíos (misma boot_id + seq_no), todos descartados por la base, con 0 duplicadas.
  - **sin_datos:** un solo incidente (16:38 hora de Lima), detectado a los 39,5 s del último reporte
    y resuelto con la primera lectura.
  - **Latencia en línea (n = 4 296):** mediana 0,217 s, p95 1,56 s.
  - **Sesgo del SHT31:** no aplica. El SHT31 mide el ambiente fuera del refrigerador
    (25,5 ± 0,4 °C), no el interior. El paso 5 del plan suponía los dos sensores dentro.

- **S12a (S5 del paper, sonda desconectada): descartada en banco.**
  - **Por qué:** la sonda está soldada y no hay una resistencia de 100 Ω–1,5 kΩ para forzar la
    línea de datos a 0 sin riesgo para el GPIO4.
  - **Cómo queda cubierta:** solo con las pruebas de software:
    - firmware: `IoT/test/test_core/test_sensores.cpp`, decodificador del scratchpad con CRC,
      valor de power-on y rango;
    - backend: fixtures `telemetria_ds18b20_desconectado.json` y `telemetria_ds18b20_crc_error.json`.

## Hallazgos

**H1. Reinicio por watchdog en bucle con el nodo sin red (abierto).**
- **Qué se vio:**
  - **Tras un reset sin red (S5 r2):** el nodo se reinició 7 veces seguidas, una cada ≈ 34 s,
    hasta que volvió la red. El evento `arranque` posterior informa `reset_reason: TASK_WDT`.
  - **Sin reset, en un corte no provocado de unas 2 h (5 nov., 21:37–23:33 UTC):** ocurrió lo
    mismo, unos 47 arranques (boots 908–954) con duraciones de ≈ 34 s o ≈ 140 s.
  - **En cortes de 10 min sin reset (S2 ×5) no ocurrió.**
- **Efecto sobre los datos:** **ninguna lectura perdida ni duplicada**, porque el buffer persiste.
  Se pierden los eventos en RAM (los `arranque` de cada reinicio) y hay una brecha de captura de
  unos 4 s por reinicio.
- **Tercera ocurrencia (6 nov., 00:13–00:17 UTC):** al pasar a WinPina 4G, el nodo quedó en una
  red **con Wi-Fi** y la laptop seguía en el hotspot, así que el broker era inalcanzable. Hubo
  5 reinicios (boots 958–962), uno cada ≈ 40 s. Sugiere que el bucle aparece con **Wi-Fi asociado y
  broker inalcanzable**, más que sin Wi-Fi.
- **En S7 (broker detenido 30 min ×5, Wi-Fi y laptop alcanzables) no ocurrió:** un solo arranque
  por repetición y ningún watchdog en el log serie de r1. Ahí el puerto del broker rechaza la
  conexión al instante.
- **Hipótesis refinada:** el bucle aparece cuando el **host** del broker es inalcanzable (sin red, o
  con Wi-Fi a otra red), no cuando el host responde y el broker está caído. Una conexión TCP/TLS
  bloqueante que espera su tiempo límite dejaría sin atender al watchdog (que también vigila la
  tarea en reposo del núcleo 0, `idle_core_mask = 1<<0`). Falta confirmarlo con el log serie del
  momento del disparo, que nombra la tarea.
- **Para el paper:** reportarlo como defecto encontrado en banco, con su efecto nulo sobre la
  integridad.

**H2. Lecturas sin hora de captura cuando un arranque nunca sincroniza.**
- **Comportamiento normal:**
  - **Primera lectura de cada arranque:** sale con `time_quality = unsynced`, y el backend la
    guarda con la hora de **llegada** en `timestamp`. Si hay estimación, la pone en
    `timestamp_estimado`.
  - **Resto de lecturas:** en un reinicio por watchdog el reloj del ESP32 se conserva, así que
    las demás salen `synced`.
- **El problema:** en el arranque 951, que duró unos 70 min sin red y sin sincronizar, las 419
  lecturas quedaron `unsynced` **sin estimación**. Por eso aparecen en el panel con la hora de
  llegada: 70 min de curva quedan comprimidos en ≈ 1 min.
- **Análisis:** el script usa `COALESCE(timestamp_estimado, timestamp)` como hora de captura.
  Excluye las lecturas sin hora ni estimación y las lista en `sin_hora.csv`.
- **Para el paper:** es una limitación de la trazabilidad temporal. Esas lecturas se podrían
  reconstruir con `uptime_ms` y los arranques vecinos.

**H3. Reconstrucción de la hora tras un reset sin red (positivo).**
- **Qué se vio (S5 r1):** después del reset y sin red, las 23 lecturas del nuevo arranque llegaron
  como `backfilled`. La primera cae 3 s después de la última del arranque anterior, así que la
  reconstrucción es coherente.

**H5. La inyección de falla `*_una_vez` no funcionaba en el banco Docker (corregido para el ensayo).**
- **Qué pasaba:** el backend corre como usuario 10001 y no podía escribir su marca en el volumen
  `/pruebas`, que es de root (`PermissionError`). En vez de fallar una vez, fallaba en cada lectura.
- **Arreglo:** se dio permiso con `chmod 1777 /pruebas`, y lo hace `paper_s6_base.sh`.
- **Pendiente:** corregirlo en `docker-compose.pruebas.yml`.

**H6. Línea base sin acuse de aplicación (estimada).**
- **Qué se hizo:** en cada repetición, durante 120 s cada COMMIT falla con el backend vivo.
- **Cómo respondió el diseño evaluado:** el nodo espera el acuse y **reenvía la misma lectura**
  (5 reenvíos por repetición), y las capturadas mientras tanto esperan en el buffer.
- **Resultado:** las 12–13 lecturas expuestas por repetición quedaron todas guardadas, 0 perdidas.
- **Estimación:** un diseño que borra con el PUBACK habría publicado cada una una vez y las habría
  perdido, unas 12 por repetición.

**H4. La IP del hotspot puede cambiar entre sesiones del teléfono.**
- **Qué se vio:** de 10.123.44.175 pasó a 10.68.89.175. Hubo que agregar la IP nueva al
  certificado del broker, con la misma CA, y apuntar el nodo a ella.
- **Dentro de una sesión:** se mantuvo estable en todos los cortes de S2 y S5.

**H7. Tiempos de la puerta acotados por el ciclo de 10 s (S9).**
- **Cómo se mide la puerta:**
  - **Muestreo:** el nodo muestrea la puerta dentro del ciclo. La lectura informa
    `puerta_abierta = t` si estuvo abierta en algún momento del ciclo, más la racha continua
    (`door_open_streak_s`) y los segundos abiertos en el ciclo.
  - **Exactitud:** la duración de la apertura que mide el nodo coincide con la marcada a ±2 s. Ese
    margen cabe en la reacción de la operadora.
- **Qué se vio:**
  - **Detección de la apertura y del cierre:** se confirmaron 7,8–7,9 s después de la marca.
  - **Estado prolongado:** se confirmó a los 67,8–67,9 s.
- **Por qué los tiempos son tan constantes:** las marcas cayeron siempre en el segundo :00 y el
  nodo captura en el :07 de cada ciclo, así que el desfase fue el mismo en las 5 repeticiones.
- **En general:**
  - **Apertura o cierre:** se detecta entre 0 y 10 s después de ocurrir (intervalo) + latencia
    (≈ 0,9 s).
  - **Estado prolongado:** se ve entre 60 y 70 s + latencia.
- **Para el paper:** reportar la cota (≤ 10 s + latencia y ≤ 70 s + latencia) junto con lo medido.

**H8. `abierto_en` de `sin_datos` es nominal; la detección real tarda 30–60 s.**
- **Qué guarda el backend:** al abrir el incidente fija `abierto_en = último reporte + umbral (30 s)`.
  La tarea que vigila el silencio corre cada 30 s, así que el instante real en que el incidente
  existe (y se notifica) es `created_at`, entre 0 y 30 s más tarde.
- **Consecuencia para las cifras:** las de "sin_datos a los ≈ 30 s" que se calcularon antes de este
  hallazgo eran nominales.
- **Lo que se reporta ahora:** las corridas se reexportaron con `detectado_utc`
  (`paper_corrida.py reexportar-incidentes`), y la tabla informa las dos cifras.
- **Medido en las 35 corridas:** la detección real estuvo **entre 30,1 y 59,9 s** tras el último
  reporte, dentro de la cota de diseño (umbral + periodo de la tarea = 30–60 s).
- **Para el paper:** reportar la detección real (30–60 s) y no `abierto_en`. Opcional para Diego:
  el panel podría mostrar `created_at` como hora de detección, o bien la tarea podría correr cada
  10 s para acotar la detección a 30–40 s.

**H9. Desconexiones MQTT espontáneas en la red doméstica, sin pérdida (S1).**
- **Qué se vio:** 89 eventos LWT `DISPOSITIVO_OFFLINE` en 12 h, todos entre las 15:00 y las 19:00
  hora de Lima (12, 31, 40 y 6 por hora). Ninguno en las 8 h siguientes.
- **Efecto:** solo 15 duraron lo bastante para dejar lecturas en el buffer (31–46 s, 25 diferidas).
  El resto se reconectó dentro del ciclo de 10 s.
- **Resultado:** 0 faltantes, y los 32 reenvíos QoS 1 terminaron en 0 duplicadas. Esto prueba la
  idempotencia (S3 del paper, entrega duplicada) en condiciones reales y no solo provocadas.
- **Causa probable:** interferencia o carga del Wi-Fi doméstico por la tarde. No se investigó más.
- **Para el paper:** mencionarlo como evidencia de robustez. Las cifras de S1 incluyen esos cortes.

**H10. Fallas físicas del nodo el 7–8 nov.: SHT31 sin respuesta y reinicios por BROWNOUT (corregido).**
- **Qué se vio (7 nov., desde 15:36 hora de Lima):** el SHT31 respondía solo en la primera lectura de
  cada arranque y luego daba `sht31_fault: no_response`. El boot_id subió de 1063 a 1113 en pocas
  horas; los motivos reportados fueron sobre todo `BROWNOUT`, más algún `POWERON` y un `TASK_WDT`.
  Al presionar el SHT31 volvían los datos por momentos.
- **Diagnóstico:**
  1. Falso contacto en las soldaduras de SDA/SCL del módulo y cable de SCL suelto en C17
     (GPIO22). Con SCL abierto el arranque decía `Sin ACK en 0x44`.
  2. Tras resoldar SCL, el módulo seguía sin alimentación propia: 3,30 V en los cables puente pero
     0 V en los pines VIN/GND del SHT31. El sensor se alimentaba a medias por las pull-up de I2C y
     respondía la dirección pero no medía (`timeout`); en ese estado también fallaban la DS18B20
     (`Timeout de conversion`) y la lectura de la puerta.
  3. Resoldados VIN y GND (8 nov., 12:39), todo volvió: 30 min con 180/180 lecturas, SHT31 y
     DS18B20 sin fallas, 0 reinicios.
- **Reinicios por energía, aparte del SHT31:**
  - con la laptop **a batería** (plan "Silent") el USB no sostiene los picos del Wi-Fi: BROWNOUT;
  - el 8 nov. a las 13:38 un movimiento de la placa dejó al nodo en bucle de BROWNOUT (~195
    arranques, boot 1146–1339; el nodo reportó `perdidas: total=9`); se resolvió reconectando;
  - con la laptop **suspendida** (14:15–17:28) el USB se apaga y el nodo no captura nada; al
    despertar arranca con `BROWNOUT`.
- **Red:** el 8 nov. la laptop pasó a Ethernet (192.168.18.21, ya incluida en el certificado del
  broker) y el nodo se reapuntó a esa IP. Con la IP vieja el nodo se colgó intentando conectar y
  salió por `TASK_WDT` (mismo patrón que H1).
- **Verificación final (8 nov., 17:28–17:58, boot 1354):** 180/180 lecturas, seq sin huecos, SHT31
  y DS18B20 180/180, 0 reinicios, 2 desconexiones MQTT sin pérdida (latencia máx. 15,2 s).
- **Para las pruebas que faltan:** alimentar el nodo con el cargador de pared de 5 V, mantener la
  laptop despierta y con la tapa abierta, fijar los cables del SHT31 con cinta para que no tiren de
  las soldaduras, y usar siempre la misma conexión de la laptop (la IP es por DHCP).
- **No afecta a las corridas del 5–6 nov.:** S1 tuvo 0 reinicios y el SHT31 respondió en las
  4 321 lecturas.

## Suites de software (6 nov. 2026, 00:20–00:45 hora de Lima)

| Suite | Commit | Resultado | Cómo |
|---|---|---|---|
| Backend (pytest) | `4a8414d` | **1 164 pasadas, 0 fallidas, 0 omitidas** | Contenedor desechable con la imagen del backend (Python 3.13) más `requirements-dev.txt`, y un PostgreSQL 17 aparte (`TEST_POSTGRES_URL`), como en la CI. No toca la base de evidencia |
| Frontend (vitest 5.0.1) | `c306819` | **348 pasadas en 41 archivos** | `npx vitest run` |
| Firmware (pruebas nativas de `src/core`) | `c8c0cac` | **134 pasadas, 0 fallidas** | `scripts/tests_nativos_msvc.cmd` (MSVC 2022), con la Unity de framework-espidf |
| End to end (Playwright 1.63.0, Chromium) | `c306819` | **26 pasadas, 0 fallidas, 5 omitidas** (31 en 7 archivos; 1.1 min) | `npx playwright test` contra la demo (`npm run dev:demo`, 127.0.0.1:5173), corrida el 6 nov. 2026 a las 02:40 hora de Lima. Las 5 omitidas son la regresión visual de `visual.spec.ts`, que solo corre con `VISUAL=1` |

Las salidas están en `evidencia/paper/suites/`. En el paper (sección 5.1) estas cifras reemplazan a
"998 backend, 318 frontend and 26 end-to-end … 134 firmware": 1,164 backend, 348 frontend, 26 end-to-end y 134 firmware, todas al 6 nov. 2026.

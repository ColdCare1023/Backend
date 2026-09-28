# Guion de la demostración en vivo — DEMO-01

**Por qué simulada:** en Boticas Perú no se puede provocar una falla térmica real sin poner en riesgo
medicamentos. La excursión se inyecta con datos marcados `"simulated": true`, desde un dispositivo de
demostración (`ESP32_DEMO_01`), nunca con la identidad del nodo instalado.

## Dos formas de inyectarla

| Opción | Cómo | Cuándo usarla |
|---|---|---|
| A · Firmware `demo` en un ESP32 de mesa | `pio run -e demo -t upload` (repo ColdCare1023/IoT); mantener **BOOT** 1 s → 4 lecturas a 12,5 °C; o por consola `demo excursion 12.5 4` | Se quiere mostrar el hardware real |
| B · Script | `python simular_excursion.py --perfil excursion --intervalo 5` | Plan de respaldo, sin depender del Wi-Fi del auditorio |

Ensayar B con `--dry-run` y contra `../banco_mqtt_local/` antes del día.

## Guion (≈ 6 min)

| t | Acción | Qué se muestra | Quién |
|---|---|---|---|
| 0:00 | Dashboard abierto en `ESP32_DEMO_01`, estado **Normal**, rótulo "En vivo" con hora | Tiempo real vía SSE | Diego |
| 0:30 | Contexto: la regla 2–8 °C es determinista; la IA solo clasifica riesgo preventivo | — | Brenda |
| 1:00 | Lanzar el perfil `excursion` (o botón BOOT) | Temperatura sube 4,8 → 12,5 °C | Brenda |
| 1:40 | La lectura cruza 8 °C | **Cambio de color** a "Excursión crítica" confirmada por la regla, sin atribuirla al modelo | Diego |
| 2:00 | Correo de alerta al QF (teléfono proyectado) | **HU-53**: envío inmediato, sin condición horaria | Diego |
| 2:40 | Reconocer y registrar acción correctiva | PENDIENTE → RECONOCIDA → ATENDIDA | Diego |
| 3:30 | Verificar integridad de la cadena del periodo | Registro SHA-256 encadenado; verificación íntegra | Diego |
| 4:30 | Perfil `falla_sensor` | Sonda en falla ⇒ "Falla de sensor", **sin** excursión falsa | Brenda |
| 5:30 | Mostrar el buffer: desconectar Wi-Fi del ESP32 demo 1 min y reconectar | `pendientes` crece y se drena en orden; nada se pierde | Brenda |

## Lista de verificación previa

- [ ] Backend reconoce `simulated=true` (etiqueta visible; excluido de KPI/IA).
- [ ] Correo de alerta configurado hacia una cuenta de demostración.
- [ ] Credenciales de `ESP32_DEMO_01` en variables de entorno (`EMQX_HOST`, `EMQX_TOKEN`, `EMQX_CA`), no en diapositivas.
- [ ] Plan B: video grabado del ensayo general.
- [ ] Después de la demo: las lecturas simuladas quedan identificadas en su propia cadena (`ESP32_DEMO_01`).

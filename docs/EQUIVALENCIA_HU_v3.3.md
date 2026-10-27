# Equivalencia de numeración de HU: código ↔ línea base v3.3 (B-4)

El código, las pruebas y parte de `docs/evidencias` se escribieron con
numeraciones anteriores del backlog (51 y 54 HU). La línea base vigente es la
**v3.3 (55 HU, Anexo 5)**. Los comentarios y nombres de archivo antiguos no se
renombran en bloque (decisión D-5): las revisiones de Alembic no pueden
cambiar de nombre y la misma etiqueta antigua se usó para cosas distintas, así
que un reemplazo automático introduciría errores. El código nuevo (desde el
26/10/2026) ya cita la numeración v3.3, y los textos que ve el usuario (PDF,
mensajes de la API) también.

| En el código / pruebas | Significa | HU en la línea base v3.3 |
|---|---|---|
| HU-53 (correo, `test_hu53_notificacion.py`, `despachador.py`) | Aviso por correo de excursión crítica | **HU-52** |
| HU-54 (SMS, `notificacion_service.py`) | Aviso por SMS (opcional) | **HU-53** |
| HU-33 (`test_hu33_estado_operativo.py`, SIN_DATOS en `/api/dispositivos/estado-operativo`) | Estado operativo y ausencia de datos | **HU-54** (en v3.3, HU-33 son las tarjetas KPI) |
| HU-56 (`test_hu56_health.py`, `test_hu56_config_despliegue.py`, README «Despliegue») | Despliegue en Railway | **HU-55** |
| HU-47 en aislamiento de corrupción, banner y snapshot forense (`gestionar_corrupcion_cadena.py`, `trazabilidad_router.py`) | Verificación e integridad de la cadena | **HU-26** |
| HU-47 en evidencia de inferencia (`ia_router.py`, `probabilidades_ia`) | Consulta de trazabilidad de la inferencia | HU-47 (coincide) |
| HU-52 (conmutación a respaldo de energía, `CONMUTACION_RESPALDO`) | Mini UPS, retirada del alcance | sin HU (el evento se conserva por compatibilidad con firmware antiguo) |
| «backlog 51 HU» / «backlog 54 HU» | Versiones previas del backlog | backlog v3.3 de 55 HU |

## Evidencias con numeración antigua

| Archivo | HU v3.3 |
|---|---|
| `docs/evidencias/HU-13_28_41_53_56_pruebas_locales.md` | HU-13, HU-28, HU-41, **HU-52**, **HU-55** |
| `docs/evidencias/HU-33_evidencia.md` | HU-33 (tarjetas KPI; coincide) |

Las HU nuevas de esta tanda ya usan v3.3: HU-29 (respaldo), HU-43/HU-44
(pendiente en el broker), HU-46 (activación con métricas), HU-54 (SIN_DATOS
persistente), HU-55 (despliegue).

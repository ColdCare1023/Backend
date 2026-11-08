# Pruebas de banco del paper (5–8 nov. 2026)

Copia versionada de `infra-local/evidencia/paper/` (carpeta ignorada por git) más el plan y los
resultados consolidados. Nodo `ESP32_FARMACIA_01`, firmware v1.2.0, intervalo de 10 s, pila local
(EMQX con TLS, backend FastAPI, PostgreSQL 17) en Docker.

## Por dónde empezar

| Archivo | Qué es |
|---|---|
| `resultados_pruebas_paper.md` | Resultados por escenario, las 36 corridas, notas, hallazgos H1–H10 y suites. Lo genera `infra-local/scripts/paper_tabla.py` |
| `plan_pruebas_paper.md` | Protocolo y meta de cada escenario |
| `hallazgos.md` | Notas por corrida, hallazgos y suites; es la fuente que se edita a mano |
| `tabla_resultados.csv` | Una fila por corrida con todas las cifras |
| `<CASO>_r<n>_<fecha>/` | Datos crudos de cada corrida: `lecturas.csv`, `mqtt.csv`, `marcas.csv`, `incidentes.csv`, `alertas.csv`, `resumen.md/json`, `corrida.json` |
| `*_ejecucion.log` | Logs de los scripts con horario fijo (`infra-local/scripts/paper_*.sh`) |
| `suites/` | Salida completa de backend (pytest), frontend (vitest), E2E (Playwright) y firmware |
| `_descartadas_hielo_01/` | Baño de hielo fallido (dos scripts a la vez); no entra en la tabla |

## Equivalencia con la Tabla 2 del paper

| Paper | Corridas |
|---|---|
| Baseline y Latency | S1 (12 h) y BASE |
| S1 | S2, S3 y S7 |
| S2 | S6 |
| S3 (entrega duplicada) | evidencia real en S1 (H9) |
| S5 | S12a, descartado en banco |
| S12 | S12b |

## Pendiente

Baño de hielo (error de la sonda y latencia de alerta), S3 de 6 h y saturación del buffer.

## Nota sobre S1

`corrida.json` de S1 dice "nodo en cargador de pared"; el nodo iba al USB de la laptop
(ver la nota de S1 y H10 en `hallazgos.md`).

No contiene credenciales: se excluyen tokens, certificados, copias de `.env` y respaldos `.dump`.

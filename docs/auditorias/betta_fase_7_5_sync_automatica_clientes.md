# BettaApp — Fase 7.5: motor operativo seguro de clientes

## Estado

Se implementó el orquestador local de sincronización bidireccional en modo seguro. El comando consulta Alegra mediante GET, prepara diagnósticos y planes en memoria, y puede aplicar cambios locales únicamente mediante una opción explícita que no fue ejecutada en esta fase.

Las escrituras externas continúan deshabilitadas. No se implementaron POST, PUT, PATCH ni DELETE hacia Alegra.

## Arquitectura reutilizada

- `AlegraReadOnlyClient`: cliente externo exclusivamente GET.
- `fetch_customer_contacts` y `build_preimport_plan`: paginación, cobertura e identidad de Fase 7.2.
- `ExternalObjectMap`: identidad externa estable por `contacts.id`.
- `BidirectionalClientSync`: planes, claves idempotentes, conflictos y capacidades.
- `apply_create_plan` y `apply_remote_updates`: servicios transaccionales locales protegidos por una opción explícita.
- `SyncAuditLog`: auditoría agregada para ejecuciones locales aplicadas.

No se crearon tablas nuevas ni un segundo sistema de mapeos.

## Comando

```text
python manage.py alegra_sync_clientes
```

El comportamiento predeterminado es diagnóstico sin escrituras. Opciones:

- `--dry-run`: diagnóstico; no modifica datos.
- `--apply-local`: habilita cambios locales y requiere `--confirm "APLICAR CAMBIOS LOCALES"`.
- `--simulate-outbound`: prepara planes salientes en memoria.
- `--limit`, `--max-pages`, `--timeout`, `--pause`, `--max-retries`.

`--apply-local` exige SQLite local `db.sqlite3`, cobertura completa, ausencia de errores de API, ausencia de IDs repetidos y ausencia de conflictos estructurales o de actualización.

No se configuró cron ni un programador de producción.

## Flujo Alegra → BettaApp

1. Consulta `/contacts` con `type=client` y `mode=advanced`.
2. Deduplicación y control de páginas mediante el servicio existente.
3. Reconocimiento por `ExternalObjectMap`.
4. Clasificación de nuevos, duplicados, inválidos y conflictos.
5. Comparación de clientes vinculados por campos compartidos.
6. Preparación de actualizaciones locales; nunca se aplican automáticamente en dry-run.

La actualización local usa transacción, bloquea el cliente y el mapeo, no reemplaza datos locales por valores externos vacíos y conserva un snapshot `last_confirmed` en metadata únicamente cuando una aplicación local futura sea autorizada.

## Flujo BettaApp → Alegra

Los clientes locales sin mapeo se detectan como pendientes. Se genera un payload allowlistado y una clave idempotente. `--simulate-outbound` produce planes `PENDING` en memoria.

No se genera un ID Alegra, no se crea un `ExternalObjectMap` activo por una simulación y no se marca `SYNCED` sin confirmación externa auténtica. Una respuesta incierta queda en `NEEDS_RECONCILIATION` y no se reintenta ciegamente.

## Estados y conflictos

Se utilizan `PENDING`, `SYNCED`, `FAILED`, `CONFLICT` y `NEEDS_RECONCILIATION`.

- Sin baseline y con diferencias: `CONFLICT`; no se aplica “último cambio gana”.
- Con baseline, cambio solo remoto: actualización local pendiente.
- Con baseline, cambios en ambos lados distintos: conflicto manual.
- Valor externo vacío frente a un valor local válido: se conserva el valor local.
- Identificación repetida: no se fusionan contactos.

## Bloqueo e idempotencia

El comando utiliza un lock exclusivo temporal por proceso para evitar solapamientos en el mismo entorno. Si existe otra ejecución, se detiene.

Las altas locales usan transacciones por contacto y las restricciones de `ExternalObjectMap`. Los planes salientes conservan la misma clave idempotente en reintentos. El motor no tiene todavía una cola persistente; por eso las operaciones salientes siguen siendo simuladas.

## Panel

El panel `/panel/integraciones/alegra/clientes/` muestra:

- clientes vinculados;
- clientes locales pendientes;
- contactos externos pendientes;
- conflictos;
- errores recientes;
- última consulta registrada;
- aviso visible de que las escrituras externas están deshabilitadas.

No ejecuta sincronización externa al abrirse y no presenta una simulación como sincronización exitosa.

## Capacidades por módulo

| Recurso | Origen | Dirección | Operaciones actuales | Escritura externa |
|---|---|---|---|---|
| Clientes | Betta después de la importación inicial | Ambos sentidos diseñados | GET y simulación | Deshabilitada |
| Productos | Betta | Alegra → Betta | GET | Deshabilitada |
| Cotizaciones | Betta | No habilitada | GET futuro | Deshabilitada |
| Ventas | Betta | No habilitada | Local | No aplica |
| Facturas | Alegra | Alegra → Betta | GET | Deshabilitada |
| Pagos | Alegra | Alegra → Betta | GET | Deshabilitada |
| Cartera | Alegra | Alegra → Betta | GET y cálculo | Deshabilitada |

El registro declarativo `INTEGRATION_CAPABILITIES` no activa escrituras por recurso.

## Pruebas

Ejecutado:

```text
python manage.py check
System check identified no issues (0 silenced).

python manage.py makemigrations --check --dry-run
No changes detected

Pruebas específicas y regresiones relevantes
Ran 46 tests in 3.770s
OK
```

Las pruebas cubren clientes con correo repetido, identidad por ID, planes entrantes y salientes, cambios remotos, cambios simultáneos, valores vacíos, respuesta perdida, reintentos idempotentes, bloqueo concurrente y ausencia de métodos de escritura externa.

## Qué quedó implementado y qué sigue simulado

Implementado:

- comando programable local;
- consulta paginada GET;
- detección entrante y saliente;
- comparación por campo;
- bloqueo de ejecuciones simultáneas;
- auditoría agregada de aplicaciones locales;
- protección de entorno y cobertura;
- integración visual del estado.

Simulado:

- creación de clientes en Alegra;
- actualización de clientes en Alegra;
- recepción de IDs externos;
- confirmación de operaciones externas;
- cola persistente de reintentos.

No se ejecutó `--apply-local` ni se habilitaron escrituras externas.

## Limitaciones y requisitos para activación real

1. Hace falta una cola persistente de operaciones pendientes antes de activar escrituras.
2. Debe definirse un adaptador autenticado de escritura separado del cliente GET-only.
3. Se requieren pruebas en sandbox de Alegra y un mecanismo de conciliación de respuestas perdidas.
4. Deben revisarse los 8 duplicados fiscales y 3 registros inválidos antes de cualquier ampliación.
5. La aplicación local debe ejecutarse con respaldo, autorización, cobertura completa y verificación posterior.
6. No se deben programar cron ni tareas de producción hasta completar la fase de habilitación externa.

## Veredicto

**Motor operativo local preparado en modo seguro; sincronización bidireccional externa no operativa.** Alegra permanece sin escrituras y BettaApp conserva el control de la lógica comercial.

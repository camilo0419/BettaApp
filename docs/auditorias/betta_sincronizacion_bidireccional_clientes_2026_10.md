# BettaApp — Sincronización bidireccional de clientes (Fase 7.1)

## Alcance y veredicto

Se implementó la infraestructura de **modo seguro** para estudiar y preparar la sincronización de clientes BettaApp ↔ Alegra. La solución no habilita una sincronización bidireccional operativa: no existen métodos HTTP de escritura, no se envían `POST`, `PUT`, `PATCH` ni `DELETE`, y no se modifican staging, clientes ni mapeos durante la simulación.

El cliente `AlegraReadOnlyClient` continúa siendo el único cliente externo y solo expone `GET`. Las operaciones de creación se representan como planes en memoria con estado `PENDING` y una clave de idempotencia estable.

## Arquitectura existente reutilizada

- `AlegraReadOnlyClient`: autenticación mediante variables de entorno, timeout y consultas GET paginadas.
- `AlegraContactStaging`: fuente local de revisión de contactos ya consultados; la clasificación persistente existente no se reemplazó.
- `ExternalSystem` y `ExternalObjectMap`: identidad estable del proveedor y vínculo `resource_type=contacts`, `external_id` ↔ `Cliente`.
- `AlegraContactImporter` y `AlegraContactReconciler`: siguen siendo el flujo persistente actual para la importación manual autorizada. El nuevo servicio no los invoca para simular.
- Panel `alegra_contactos_catalogo`: conserva sus acciones actuales de consulta, revisión, vinculación e importación local.

No se creó una segunda tabla de mapeos ni una segunda arquitectura de importación.

## Componentes creados

### `tienda/services/alegra_bidirectional_clients.py`

Servicio sin efectos secundarios que contiene:

- Clasificación en memoria de muestras de Alegra.
- Detección de identificación repetida entre contactos externos.
- Uso de identificación normalizada como señal; el nombre por sí solo nunca confirma una relación.
- Reconocimiento de IDs externos ya mapeados.
- Generación de payload allowlistado para una futura creación desde BettaApp.
- Clave de idempotencia SHA-256 estable por dirección, identidad y payload.
- Detección de cambios concurrentes por campo compartido.
- Estado `NEEDS_RECONCILIATION` cuando una creación externa pudo ser aceptada pero se perdió la respuesta.
- `SafeAlegraWriteAdapter`, cuyo único método devuelve un plan simulado y no implementa verbos HTTP de escritura.

Estados operativos representados:

`PENDING`, `SYNCED`, `CONFLICT`, `FAILED` y `NEEDS_RECONCILIATION`. En esta fase solo se generan planes simulados; no se marca ningún registro real como `SYNCED`.

### Panel administrativo

El catálogo de clientes externos muestra un aviso visible de modo seguro y contadores de:

- mapeos activos de clientes;
- contactos pendientes de revisión;
- conflictos;
- errores;
- contactos externos sin vínculo.

Los contadores consultan staging y mapeos existentes. No crean `ExternalSystem`, no sincronizan y no alteran datos al abrir la pantalla.

## Identidad y clasificación

El ID de contacto de Alegra es la identidad externa principal. El PK de `Cliente` es la identidad local. Un mapeo activo no se confirma por nombre.

Reglas aplicadas por `BidirectionalClientSync.classify_initial_import`:

1. ID externo ya mapeado → `linked`.
2. Falta nombre o identificación → `incomplete`.
3. La misma identificación normalizada aparece en varios contactos externos → `conflict`; no se fusionan.
4. Hay varios clientes locales con la identificación → `conflict`.
5. Hay un único candidato local por identificación → `probable`; requiere revisión y vinculación explícita.
6. No hay candidato → `new`.

La clasificación es una función en memoria. No importa clientes ni crea puntos de venta, proyectos, solicitudes o relaciones comerciales.

## Importación inicial desde Alegra

La consulta real existente continúa siendo paginada y limitada por `AlegraContactImporter`. Para una simulación de importación inicial se pasan a `classify_initial_import` las filas obtenidas por GET y un conjunto explícito de clientes/mapeos locales. El servicio no persiste los resultados.

El límite debe definirse por ejecución; una muestra no se interpreta como catálogo completo. La incorporación local posterior queda sujeta al flujo manual existente y a la revisión de coincidencias.

## Creación desde BettaApp — diseño futuro

`plan_betta_creation(cliente)` genera un payload limitado a campos compartidos no vacíos y retorna `PENDING`. No crea un contacto en Alegra, no registra un mapeo y no cambia el cliente local.

Cuando exista autorización futura para escrituras, el proceso deberá registrar la operación pendiente antes del POST, usar una clave idempotente y conciliar la respuesta antes de marcarla como confirmada. Si la respuesta se pierde después de que Alegra acepte el POST, el estado correcto es `NEEDS_RECONCILIATION`; el servicio no reintenta ciegamente.

## Creación detectada desde Alegra — diseño futuro

`plan_alegra_creation(row)` representa una incorporación local pendiente a partir de un contacto externo identificado. No crea un `Cliente`, no crea un punto de venta y no establece un mapeo en esta fase.

Antes de autorizarla será obligatorio revisar el ID externo, la identificación, candidatos locales y posibles duplicados. La repetición de identificación no autoriza una fusión.

## Actualizaciones y conflictos

Los campos compartidos se comparan con un baseline de última sincronización. Si BettaApp y Alegra cambiaron el mismo campo desde ese baseline y los valores difieren, `detect_update_conflicts` devuelve el campo para revisión. No se aplica “último cambio gana” y no se sobrescriben campos locales vacíos con información ausente.

Los puntos de venta, proyectos, solicitudes, cotizaciones y demás reglas comerciales siguen siendo propiedad de BettaApp.

## Seguridad

- No se almacenan tokens ni secretos.
- No se agregaron métodos de escritura al cliente HTTP.
- El panel sigue protegido por los decoradores de personal autorizado y las operaciones existentes usan POST/CSRF.
- La simulación no ejecuta llamadas externas ni modifica bases de datos.
- Los payloads simulados se construyen mediante lista blanca; no incluyen encabezados ni credenciales.

## Pruebas ejecutadas

Archivo: `tienda/test_alegra_bidirectional_clients.py`.

Se cubrieron clasificación inicial, vínculo por ID externo, coincidencia probable por identificación, identificación repetida, nombre sin identificación, planes simulados en ambas direcciones, idempotencia, respuesta perdida, conflictos de actualización y ausencia de verbos de escritura en el adaptador.

También se ejecutaron las pruebas existentes de cliente HTTP y contactos:

```text
python manage.py check
System check identified no issues (0 silenced).

python manage.py makemigrations --check --dry-run
No changes detected

python manage.py test tienda.test_alegra_bidirectional_clients tienda.test_alegra_contacts tienda.test_alegra_client
Ran 21 tests in 3.443s
OK
```

No se aplicaron migraciones y no se modificó la base SQLite existente.

## Limitaciones y riesgos abiertos

1. No existe todavía una cola persistente de operaciones pendientes; los planes de esta fase son in-memory. Debe diseñarse antes de autorizar escrituras reales.
2. La recuperación de una respuesta perdida requiere un mecanismo de búsqueda y revisión basado en datos verificables de Alegra; no debe resolverse con un POST repetido.
3. La detección de conflicto requiere conservar un baseline confiable de última sincronización, aún no incorporado como modelo nuevo.
4. La política de actualización campo a campo necesita validarse contra timestamps y capacidades reales de Alegra antes de habilitarse.
5. La sincronización real de creación en cualquiera de las dos direcciones permanece desactivada y requiere autorización, pruebas de sandbox y controles operativos adicionales.

## Migraciones

No hubo cambios de esquema. `makemigrations --check --dry-run` no detectó migraciones pendientes.

## Archivos modificados

- `tienda/services/alegra_bidirectional_clients.py` — nuevo servicio seguro de planificación.
- `tienda/views.py` — indicadores de modo seguro en el catálogo existente.
- `tienda/templates/tienda/panel/alegra_contactos.html` — aviso e indicadores visibles.
- `tienda/test_alegra_bidirectional_clients.py` — pruebas de la arquitectura simulada.
- `docs/auditorias/betta_sincronizacion_bidireccional_clientes_2026_10.md` — este informe.

## Conclusión

La arquitectura de identidad y planificación segura está preparada para una fase posterior, pero la sincronización bidireccional **no está operativa**. Alegra continúa siendo consultado exclusivamente mediante GET y cualquier escritura futura requiere una autorización expresa y una etapa independiente de habilitación.

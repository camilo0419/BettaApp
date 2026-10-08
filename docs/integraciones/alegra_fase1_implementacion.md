# BettaApp — Implementación Fase 1 de Alegra

## Alcance

Esta fase agrega consulta, staging, revisión, vinculación e importación controlada de productos de Alegra. No implementa facturación, inventario, clientes, cotizaciones externas, cartera, pagos, notas crédito ni escrituras a Alegra.

## Modelos creados

- `ExternalSystem`: proveedor y ambiente; no contiene secretos.
- `ExternalObjectMap`: identidad externa estable por sistema/recurso/ID, con GenericForeignKey opcional al objeto local.
- `AlegraItemStaging`: snapshot depurado de ítems, precio de referencia, categoría externa, revisión, coincidencia/importación y datos técnicos allow-listed.
- `SyncAuditLog`: operación, recurso, usuario, resultado y error depurado.

Los modelos no alteran `Producto`, `Categoria`, `Cliente`, `Solicitud`, `Cotizacion` ni los modelos de producción. La migración creada es `tienda/migrations/0019_alegra_integration.py`.

## Servicio

`tienda/services/alegra_import.py` reutiliza `AlegraReadOnlyClient` y expone:

- `sync(limit=...)`: consulta paginada de `/items` en `mode=advanced`.
- upsert idempotente por sistema e ID externo.
- coincidencia local por nombre exacto sin sobrescritura.
- estados pendiente, coincidencia, conflicto, importado, ignorado y error.
- `link_item(...)`: vinculación manual sin cambiar el producto local.
- `import_item(...)`: crea producto local inactivo, con categoría y tipo de cálculo elegidos; deja precios en cero para revisión manual.
- `ignore_item(...)`: marca el staging como ignorado.

La información técnica persistida se limita a campos permitidos. No se guardan tokens, encabezados de autenticación ni respuestas completas indiscriminadas.

## Rutas administrativas

Todas requieren usuario activo `is_staff`; las vistas de consulta requieren `view_alegraitemstaging` y las acciones de cambio requieren `change_alegraitemstaging`, salvo superusuario.

| Ruta | Método | Uso |
|---|---|---|
| `/panel/integraciones/alegra/` | GET | Resumen, métricas y última operación |
| `/panel/integraciones/alegra/catalogo/` | GET | Catálogo staging, búsqueda, filtros y paginación |
| `/panel/integraciones/alegra/sincronizar-items/` | POST + CSRF | Consultar ítems de Alegra; límite 1–300 |
| `/panel/integraciones/alegra/items/<id>/vincular/` | POST + CSRF | Vinculación manual a producto Betta |
| `/panel/integraciones/alegra/items/<id>/importar/` | POST + CSRF | Crear producto local inactivo |
| `/panel/integraciones/alegra/items/<id>/ignorar/` | POST + CSRF | Ignorar staging |
| `/panel/integraciones/alegra/historial/` | GET | Historial de sincronización |

La navegación se añadió al panel existente bajo “Integraciones · Alegra”. El flujo público, cotizaciones, solicitudes, producción y portal cliente no fueron modificados.

## Procedimiento de importación

1. Entrar al panel con un usuario administrativo autorizado.
2. Abrir Integraciones · Alegra y ejecutar “Consultar Alegra” con un límite acotado.
3. Revisar el catálogo y resolver coincidencias/conflictos.
4. Vincular si el producto Betta ya existe.
5. Para un producto nuevo, seleccionar categoría activa y tipo de cálculo; importarlo.
6. Revisar manualmente precio por unidad/m², campos, opciones, imágenes y publicación.
7. Mantenerlo inactivo hasta completar la configuración comercial.

No se asigna categoría por nombre externo, no se crean campos dinámicos, no se activan productos y no se copian automáticamente precios externos a la fórmula comercial.

## Migraciones y base de datos

Se creó la migración `0019_alegra_integration.py`, dependiente de `0018_clientepuntoventa`. Antes de aplicar migraciones se creó el respaldo local `db.sqlite3.fase1-backup-20261007`. En este entorno de trabajo no está instalado Django, por lo que no fue posible ejecutar `manage.py migrate`; la aplicación de la migración queda pendiente de realizarse en el entorno local con dependencias instaladas. No se tocó producción.

Los campos usan tipos compatibles con SQLite y MySQL/MariaDB: `CharField`, `TextField`, `DecimalField`, `DateTimeField`, `JSONField`, claves foráneas e índices normales. No se usan operaciones específicas de un motor.

## Pruebas

Se añadieron `tienda/test_alegra_import.py` y se conservó `tienda/test_alegra_client.py`. Cubren:

- paginación y upsert idempotente;
- conflictos por nombres duplicados;
- importación inactiva y sin precios comerciales automáticos;
- vinculación sin alterar producto existente;
- auditoría de errores API;
- restricción de usuario no staff;
- POST obligatorio y CSRF;
- cliente HTTP limitado a GET y ausencia de escritura externa.

No pudieron ejecutarse `python manage.py check`, `python manage.py makemigrations --check` ni las pruebas porque el checkout no tiene Django instalado y no existe `venv`/`.venv`. La sintaxis Python de los archivos nuevos sí fue validada con `py_compile`.

## Variables de entorno

El cliente conserva el contrato existente:

- `ALEGRA_EMAIL`
- `ALEGRA_API_TOKEN`
- `ALEGRA_BASE_URL`
- opcionalmente `ALEGRA_TIMEOUT`

Las credenciales solo se leen desde variables de entorno. Nunca se almacenan en `ExternalSystem.config` ni se exponen en templates o logs.

## Riesgos conocidos y decisiones pendientes

- La coincidencia usa nombre exacto porque `Producto` no tiene referencia local; no es una identidad definitiva.
- La categoría externa no se transforma automáticamente en categoría local.
- Variantes, kits, inventario, listas de precios y campos fiscales quedan fuera de la fase.
- El permiso de integración debe asignarse a los grupos administrativos apropiados después de aplicar la migración.
- La aplicación de la migración debe verificarse en SQLite y posteriormente en una copia MySQL/MariaDB antes de producción.
- La sincronización todavía es una acción de panel; no hay tareas programadas, webhooks ni cola asíncrona.

## Preparación para producción y fases futuras

Antes de producción: aplicar y revisar migraciones en staging, verificar permisos, configurar variables sin versionarlas, probar respaldo/restauración, ejecutar smoke tests y confirmar que no hay llamadas POST/PUT/PATCH/DELETE al proveedor.

La siguiente fase puede extender el mismo patrón para clientes y recursos contables mediante nuevos adaptadores y staging, sin cambiar la lógica comercial. Facturación, pagos, cartera, notas crédito y webhooks permanecen deliberadamente fuera de Fase 1.
## Extensión Fase 1.1

La conciliación inteligente, las operaciones masivas y la vista previa de importación están documentadas en [alegra_fase1_1_conciliacion.md](alegra_fase1_1_conciliacion.md). Esta extensión agrega únicamente estado de clasificación al staging y conserva el principio de solo lectura frente a Alegra.

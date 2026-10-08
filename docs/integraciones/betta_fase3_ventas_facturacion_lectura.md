# BettaApp — Fase 3: ventas y consulta de facturación Alegra

## 1. Alcance y arquitectura

BettaApp conserva la autoridad sobre la operación comercial: clientes, cotizaciones, ventas, producción y precios. Alegra se consulta como fuente externa de documentos contables. En esta fase no se emiten, editan, anulan ni pagan facturas en Alegra.

La separación queda así:

- `Venta` y `VentaItem`: operación comercial local.
- `AlegraInvoiceStaging`: snapshot local de facturas consultadas mediante GET.
- `VentaFacturaAlegra`: vínculo explícito y auditable entre una venta y una factura externa.
- `ExternalObjectMap`: identidad estable para contactos y futuros documentos.
- `SyncAuditLog`: resultado técnico de cada sincronización.

## 2. Modelos

### Venta

Incluye consecutivo interno único, cliente, punto de venta opcional, proyecto, cotización y solicitud de origen, fecha, responsable, estado comercial, totales, observaciones y usuarios de auditoría.

Los estados son `borrador`, `confirmada`, `en_proceso`, `completada` y `cancelada`. El estado comercial no representa facturación ni pago.

### VentaItem

Guarda snapshot de producto, descripción, cantidad, unidad, precio unitario, descuento, impuesto y total. Los cambios posteriores del producto no alteran la venta histórica.

### AlegraInvoiceStaging

Guarda el ID externo único, número, prefijo, fechas, cliente externo, identificación, importes disponibles, moneda, estado externo, saldo, referencia, conceptos normalizados allow-listed, JSON de origen, hash, fechas de sincronización y estado de conciliación. Los resultados repetidos hacen upsert por `(sistema, recurso, external_id)`.

### VentaFacturaAlegra

Permite una relación explícita y no necesariamente uno a uno. Cada vínculo exige evidencia comercial y usuario confirmador. La restricción evita repetir el mismo par venta-factura.

## 3. Conversión de cotizaciones

La acción `Confirmar venta` solo acepta cotizaciones aprobadas o ya convertidas. Dentro de una transacción:

1. Verifica que existan ítems activos.
2. Reutiliza una venta existente si la cotización ya fue convertida.
3. Crea la venta confirmada y copia los ítems como snapshots.
4. Mantiene cliente, proyecto, punto de venta y solicitud.
5. Marca la cotización como convertida.

No se recalculan ni sustituyen los valores de la cotización desde Alegra.

## 4. Ventas directas

El panel permite crear una venta en borrador y agregar ítems usando el catálogo local. Los precios iniciales se toman de la política de Betta existente; no se crea otro motor de precios ni se consultan listas comerciales de Alegra.

## 5. Endpoints Alegra verificados

Se verificó en la documentación oficial:

- `GET /api/v1/invoices`: listado de facturas de venta, paginación con `start` y `limit`, máximo documentado de 30 por solicitud, filtros por estado, cliente, identificación y fechas, y metadatos opcionales.
- `GET /api/v1/invoices/{id}`: detalle de una factura por ID; permite campos adicionales documentados como `pdf`, `xml`, `comments` y `events`. Esta fase no solicita archivos ni campos adicionales.

El cliente existente usa Basic Auth desde variables de entorno, timeout, manejo de 401/403/429 y no expone encabezados. El importador solo invoca `paged_get("/invoices")`; no hay POST, PUT, PATCH ni DELETE.

Referencia oficial: [lista de facturas de venta](https://developer.alegra.com/reference/get_invoices) y [detalle de factura](https://developer.alegra.com/reference/get_invoices-id).

## 6. Staging y conciliación

La sincronización admite límite configurable entre 1 y 300 registros, procesa páginas de hasta 30, conserva staging anterior ante errores y registra auditoría.

La conciliación de cliente prioriza:

1. `ExternalObjectMap` del contacto Alegra.
2. Identificación tributaria normalizada cuando produce un único candidato.
3. Vinculación manual desde el detalle de factura.

No se vincula únicamente por nombre, correo, fecha o valor. Una ausencia de vínculo no demuestra que no exista facturación.

## 7. Paneles y rutas

Ventas:

- `/panel/ventas/`
- `/panel/ventas/crear/`
- `/panel/ventas/<id>/`
- `/panel/ventas/<id>/items/nuevo/`
- `/panel/ventas/<id>/confirmar/`
- `/panel/ventas/<id>/cancelar/`
- `/panel/ventas/informes/`

Facturas Alegra:

- `/panel/integraciones/alegra/facturas/`
- `/panel/integraciones/alegra/facturas/sincronizar/`
- `/panel/integraciones/alegra/facturas/<staging_id>/`
- `/panel/integraciones/alegra/facturas/<staging_id>/vincular-venta/`

Las sincronizaciones y vinculaciones son POST con CSRF y permisos administrativos. La interfaz deshabilita el botón de sincronización durante el envío para evitar dobles envíos.

## 8. Informes

El informe separa tres bloques: ventas confirmadas y valor vendido Betta; cantidad y total de facturas Alegra; y conciliación de ventas/facturas vinculadas. No suma venta y factura como ingresos independientes, no interpreta saldo como recaudo y muestra la fecha de última sincronización.

## 9. Pruebas y migración

Se agregó `tienda/test_fase3_ventas.py` con pruebas de:

- venta directa y snapshot histórico;
- conversión idempotente de cotización;
- staging paginado e idempotente;
- conciliación por identificación;
- errores API sin pérdida de staging;
- vínculo manual con evidencia y sin duplicados;
- POST, CSRF y permisos.

Validación local: `python manage.py check` correcto; `python manage.py makemigrations --check --dry-run` sin cambios pendientes; pruebas específicas 7/7 correctas. La suite general ejecutó 121 pruebas: 119 correctas y 2 errores preexistentes de multimedia por `PermissionError [WinError 5]` al crear/eliminar directorios temporales del entorno.

Se generaron y aplicaron localmente `tienda/migrations/0023_alegrainvoicestaging_venta_ventafacturaalegra_and_more.py` y `0024_alegrainvoicestaging_line_items.py`. Antes se respaldó SQLite en `db.sqlite3.fase3-backup-20261008` y `db.sqlite3.fase3-1-backup-20261008`.

## 10. Limitaciones

- Las credenciales `ALEGRA_EMAIL`, `ALEGRA_API_TOKEN` y `ALEGRA_BASE_URL` no están disponibles en el entorno de esta ejecución; no se hizo una consulta real externa. La prueba de conexión queda para la interfaz local con las variables configuradas.
- El staging conserva el JSON recibido para trazabilidad local; debe revisarse la política de retención antes de producción.
- No se implementaron pagos, cartera, notas crédito, inventarios, emisión de facturas ni cambios de facturas.
- La seguridad de consecutivos depende de la restricción única y debe complementarse con una estrategia de secuencia transaccional específica si se habilita alta concurrencia en producción.

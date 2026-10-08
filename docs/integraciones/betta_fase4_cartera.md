# BettaApp — Fase 4: cartera y gestión de cobros

## Alcance

BettaApp administra el seguimiento comercial y las gestiones de cobro. Alegra continúa siendo la fuente externa para saldos, facturas y pagos contables. Esta fase solo consulta Alegra mediante GET; no registra pagos, modifica facturas, altera saldos ni emite documentos.

## Endpoints Alegra verificados

Se verificó la documentación oficial:

- `GET /api/v1/payments`: consulta pagos registrados. Usa `type=in` para ingresos de clientes y `type=out` para egresos; admite `start`, `limit` máximo 30, orden, `client_id`, `conciliation_id`, `id`, `includeUnconciliated` y `fields`.
- `GET /api/v1/payments/{id}`: detalle de un pago; permite campos adicionales como comentarios y, para Colombia, `voucherNumber` según la documentación.
- `GET /api/v1/invoices`: fuente de facturas de venta y saldos disponibles en el staging de Fase 3.

Fuentes: [consulta de pagos](https://developer.alegra.com/reference/get_payments), [detalle de pago](https://developer.alegra.com/reference/get_payments-id), [facturas de venta](https://developer.alegra.com/reference/get_invoices).

La API no se trató como si todos los pagos tuvieran una única factura: se conserva una lista de IDs de facturas cuando la respuesta la proporciona y se guarda el JSON original para revisión.

## Modelos

### `AlegraPaymentStaging`

Staging idempotente por `(ExternalSystem, external_id)`. Contiene fecha, cliente externo, valor, moneda, medio de pago, estado, referencias de facturas, hash, JSON original, fechas de consulta y estado de conciliación. Solo sincroniza ingresos (`type=in`). Consultas parciales no eliminan registros anteriores.

### `CarteraGestion`

Historial no destructivo de llamadas, correos, WhatsApp, reuniones u otras gestiones. Puede referenciar cliente y factura, tiene responsable, resultado, observaciones y próxima acción. No envía comunicaciones automáticamente.

### `CompromisoPago`

Registra fecha y valor comprometido, factura opcional, responsable, estado y observaciones. Cumplir o cancelar el compromiso es una decisión local; nunca modifica el saldo de Alegra.

### `CarteraResponsable`

Asigna un único responsable activo por cliente y conserva las asignaciones anteriores como inactivas. Reutiliza usuarios existentes y registra quién realizó la asignación.

## Motor de cartera

`tienda/services/cartera.py` calcula cada factura individualmente y luego agrega resultados sin duplicar por vínculos venta-factura.

- El saldo prioritario es `AlegraInvoiceStaging.balance`.
- Si el saldo no existe, el estado es `sin_informacion`.
- Si el saldo es cero, el estado es `pagada`.
- Si el saldo es menor que el total, el estado es `parcialmente_pagada`.
- Si existe saldo completo, el estado es `pendiente` o `vencida` según la fecha de vencimiento.
- Facturas anuladas se excluyen de la cartera calculable.
- El valor abonado solo se calcula cuando existen total y saldo confiables.

La antigüedad usa la fecha de vencimiento y la fecha de corte local (`timezone.localdate()`): `no_vencida`, `1_30`, `31_60`, `61_90`, `mas_90` y `sin_informacion`.

Los datos actuales no son snapshots históricos. Seleccionar una fecha de corte anterior no reconstruye el saldo que existía en esa fecha; para eso se requerirían snapshots o movimientos confiables.

## Panel y rutas

- `/panel/cartera/`: resumen, filtros por factura/cliente/estado/antigüedad, tabla de saldos, pagos consultados y controles de sincronización.
- `/panel/cartera/clientes/<id>/`: detalle por cliente con facturas, pagos, gestiones, compromisos y responsable.
- `/panel/cartera/clientes/<id>/gestiones/nueva/`: registro POST de gestión.
- `/panel/cartera/clientes/<id>/compromisos/nuevo/`: registro POST de compromiso.
- `/panel/cartera/clientes/<id>/responsable/`: asignación POST de responsable.
- `/panel/integraciones/alegra/facturas/sincronizar/`: actualización de facturas reutilizando Fase 3.
- `/panel/integraciones/alegra/pagos/sincronizar/`: consulta GET de pagos recibidos a través del cliente existente.

Las operaciones locales usan permisos administrativos, POST y CSRF. La sincronización muestra resultados de consultados, nuevos, actualizados y errores mediante mensajes del panel. No hay sincronización automática configurada.

## Indicadores

Se muestran saldo pendiente conocido, saldo vencido conocido, porcentaje vencido, clientes con saldo, facturas pendientes y vencidas, antigüedad y compromisos próximos/incumplidos. No se calculan DSO ni rotación por falta de una serie histórica confiable.

Ventas Betta, facturación Alegra, pagos y cartera se mantienen como fuentes separadas. No se suman facturas y pagos como ingresos independientes.

## Migración y respaldo

Se generó y aplicó localmente `tienda/migrations/0025_alter_alegrainvoicestaging_external_status_and_more.py`, que agrega staging de pagos, gestiones, compromisos, responsables y estados de factura abiertos/cerrados.

Respaldo creado antes de aplicar: `db.sqlite3.fase4-backup-20261008`. No se modificó producción ni la configuración de base de datos.

## Pruebas

`tienda/test_fase4_cartera.py` cubre estados pendiente, vencida, parcialmente pagada, pagada, sin saldo y anulada; rangos de antigüedad; sincronización de ingresos con paginación e idempotencia; errores sin borrar staging; referencias de pagos; validación cliente-factura; compromisos; permisos y POST.

Validaciones ejecutadas:

- `python manage.py check`: correcto.
- `python manage.py makemigrations --check --dry-run`: sin cambios pendientes.
- Pruebas específicas de Fase 4: 5/5 correctas.
- Suite general: 126 pruebas; 124 correctas y 2 errores preexistentes `PermissionError [WinError 5]` en pruebas multimedia al crear/eliminar directorios temporales del entorno.

## Limitaciones y datos pendientes de validar

- Las credenciales de Alegra no están disponibles en el entorno de Codex; no se ejecutó una sincronización real ni se puede reportar cantidad real de facturas/pagos.
- La estructura concreta de distribución de pagos por factura debe confirmarse con una respuesta real de la cuenta. Se conserva la lista disponible sin inventar una asignación.
- Un saldo actual no es un saldo histórico. La antigüedad histórica requiere snapshots.
- Los compromisos no se marcan cumplidos automáticamente por fecha, valor o coincidencia de pago.
- El JSON original contiene datos externos y requiere una política de retención antes de producción.

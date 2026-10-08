# BettaApp Fase 5 — Centro de control comercial

## Resumen

Se añadió un centro de control administrativo que consolida pendientes comerciales,
operativos y de cartera sin reemplazar los módulos existentes. BettaApp continúa
siendo el sistema maestro. El centro no hace peticiones a Alegra: consume únicamente
los datos locales ya sincronizados y muestra advertencias cuando la información
financiera no es suficiente.

## Auditoría y reutilización

La implementación reutiliza:

- `SolicitudTarea` para tareas asignables, fechas límite, prioridades y estados.
- `SolicitudNovedad` como historial existente de cambios de tareas y operación.
- `Notificacion` para alertas internas idempotentes.
- `CarteraGestion`, `CompromisoPago`, `AlegraInvoiceStaging` y `services.cartera`.
- Los listados y detalles existentes de solicitudes, cotizaciones, ventas, producción y cartera.

No se creó un modelo paralelo de tareas ni se modificaron modelos comerciales.

## Indicadores

El servicio `tienda.services.centro_control.centro_control_snapshot` calcula:

- Comercial: solicitudes en estados nueva/revisión/pendiente de información,
  cotizaciones abiertas, aprobadas sin venta y ventas en estados no finales.
- Operación: solicitudes con producción en curso, entregas pendientes y tareas
  vencidas con `fecha_limite` real.
- Cartera: facturas vencidas con saldo conocido, compromisos próximos y gestiones
  cuyo seguimiento ya venció.

Los enlaces abren el registro original. Las facturas anuladas y los saldos
desconocidos no se convierten en cartera vencida. Si no existe staging de facturas,
se muestra una advertencia y no se presenta un saldo como histórico.

## Alertas internas

La acción manual **Actualizar alertas** ejecuta
`refresh_control_alerts(actor)`. Actualmente genera alertas verificables para:

- Tareas atrasadas asignadas.
- Cotizaciones abiertas con fecha de vencimiento dentro de los próximos siete días.
- Compromisos de pago pendientes con fecha vencida.

La operación es POST con CSRF y permiso de panel. Se evita el duplicado buscando
una notificación no leída con el mismo usuario, título y destino. No se envían
correos, WhatsApp ni comunicaciones externas, y no existe cron configurado.

## Rutas

- `/panel/centro-control/` — dashboard de pendientes.
- `/panel/centro-control/actualizar-alertas/` — POST manual de alertas.
- Los enlaces del dashboard apuntan a los paneles existentes de solicitudes,
  cotizaciones, ventas, producción, cartera y facturas Alegra.

El acceso requiere usuario autenticado, activo y `is_staff`, siguiendo el patrón
actual del panel administrativo. Los datos de tareas operativas se filtran por
responsable para usuarios no staff; un staff puede ver el consolidado del equipo.

## Migraciones y compatibilidad

No se generó migración en Fase 5 porque se reutilizaron modelos y tablas existentes.
No se modificó la configuración de SQLite, MySQL/MariaDB ni Alegra. No hay cambios
de esquema que aplicar localmente.

## Pruebas

`tienda.test_fase5_centro_control` cubre:

- Consolidación de pendientes y advertencia sin facturas.
- Visibilidad de tareas por responsable.
- Idempotencia de alertas.
- Restricción de acceso a staff.
- Método POST para actualizar alertas y render del panel.

También debe ejecutarse la suite general. Los dos errores `PermissionError` de
archivos multimedia preexistentes se reportan por separado y no se atribuyen a
este módulo.

## Limitaciones y próximos pasos

- La vista no ejecuta sincronizaciones de Alegra; la actualización financiera debe
  hacerse desde los módulos existentes de facturas/pagos.
- No se inventan vencimientos de cotizaciones, producción o cartera cuando el
  modelo no aporta una fecha confiable.
- La información de Alegra es el estado conocido de la última sincronización,
  no una reconstrucción histórica.
- La ejecución programada en cPanel queda para una fase posterior y deberá usar
  un mecanismo de bloqueo para evitar concurrencia.
- Se pueden ampliar las alertas de cotizaciones próximas a vencer y producción
  atrasada cuando se definan responsables y fechas operativas confiables.

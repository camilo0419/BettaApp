# BettaApp — Fase 2.1: estructura comercial

## Alcance

Esta fase conecta la estructura administrativa existente de clientes, puntos de venta, proyectos, solicitudes y cotizaciones. BettaApp continúa siendo el sistema maestro. No se modificó la integración HTTP de Alegra, no se implementaron escrituras hacia Alegra, facturación, cartera, inventarios ni cambios de precios.

## Modelos y relaciones

- `Cliente` mantiene su identidad y sus datos actuales.
- `ClientePuntoVenta` mantiene la relación `cliente -> puntos de venta`; un punto pertenece a un único cliente y no se exige para crear un cliente.
- `Proyecto` incorpora `puntos_venta`, una relación ManyToMany opcional con `ClientePuntoVenta`. Un proyecto puede no tener sedes o abarcar varias sedes.
- `Solicitud` incorpora `punto_venta`, una relación opcional con `ClientePuntoVenta` y `SET_NULL` para conservar históricos.
- `Cotizacion` incorpora `punto_venta`, también opcional y con `SET_NULL`.

Las validaciones de formularios y modelos impiden asociar puntos de venta o proyectos de otro cliente. Las relaciones futuras con Alegra siguen representadas mediante los mapeos externos existentes; Alegra no es requisito para crear registros comerciales.

## Migración y compatibilidad

Se generó y aplicó localmente `tienda/migrations/0022_cotizacion_punto_venta_proyecto_puntos_venta_and_more.py`. La migración es aditiva: crea la tabla intermedia del ManyToMany y dos claves foráneas opcionales, sin renombrar ni eliminar campos existentes.

Antes de aplicarla se creó el respaldo local `db.sqlite3.fase2_1-backup-20261008`. No se modificó la configuración de base de datos ni se conectó a producción.

## Formularios y filtrado dinámico

`ProyectoForm`, `SolicitudClienteForm` y `CotizacionForm` filtran los puntos de venta por el cliente seleccionado, conservan selecciones durante la edición y permiten dejar el campo vacío. `SolicitudProyectoForm` conserva la compatibilidad con el flujo por proyecto y valida las referencias cruzadas.

El panel usa el endpoint administrativo:

`GET /panel/ajax/clientes/<cliente_id>/puntos-venta/`

El endpoint exige usuario administrativo, devuelve únicamente puntos activos del cliente indicado y se integra con el JavaScript de selects relacionados existente. La validación definitiva se ejecuta siempre en el servidor; no se confía en filtros del navegador.

## Ficha y navegación administrativa

La ficha de cliente conserva las secciones existentes y muestra los puntos de venta, proyectos, solicitudes, cotizaciones y vínculo externo de Alegra. El detalle de proyecto muestra sus sedes, solicitudes y cotizaciones. Los detalles de solicitud y cotización muestran el punto de venta cuando existe.

Rutas principales para validación manual:

- `/panel/clientes/`
- `/panel/clientes/<id>/`
- `/panel/clientes/<id>/puntos-venta/nuevo/`
- `/panel/clientes/<id>/puntos-venta/<punto_id>/editar/`
- `/panel/proyectos/`
- `/panel/proyectos/crear/`
- `/panel/proyectos/<id>/editar/`
- `/panel/solicitudes/`
- `/panel/cotizaciones/`
- `/panel/cotizaciones/crear/`

Las operaciones de modificación existentes mantienen POST, CSRF y los permisos administrativos definidos por el panel.

## Decisiones técnicas

1. No se crean puntos de venta automáticamente desde contactos o direcciones de Alegra.
2. Un proyecto puede tener múltiples sedes y no se fuerza una sede cuando ya se eligió un proyecto.
3. Las asociaciones de solicitud y cotización son opcionales para no afectar históricos ni flujos existentes.
4. Se usa `SET_NULL` en las nuevas relaciones directas para evitar pérdida de documentos históricos si un punto deja de estar disponible.
5. La restricción de punto principal activo por cliente existente se conserva.
6. Se usan `select_related`/`prefetch_related` en detalles para evitar consultas repetitivas en las relaciones nuevas.

## Pruebas

Se agregó `tienda/test_fase2_1_relaciones.py`, con cobertura de:

- proyectos con varias sedes y sin sedes;
- validación de puntos de otro cliente en proyecto, solicitud y cotización;
- filtrado y conservación de selecciones en formularios;
- solicitudes y cotizaciones con y sin punto;
- unicidad del punto principal;
- permisos y CSRF del panel.

Resultados locales:

- `python manage.py check`: correcto.
- `python manage.py makemigrations --check --dry-run`: sin cambios pendientes.
- Pruebas específicas: 22 pruebas, todas correctas.
- Suite general: 114 pruebas; 112 correctas y 2 errores en pruebas preexistentes de archivos multimedia por `PermissionError [WinError 5]` al crear/eliminar directorios temporales del entorno de ejecución. No corresponden a la estructura comercial implementada.

## Limitaciones pendientes

- La integridad entre tablas se garantiza mediante formularios y `full_clean`; un uso directo de `ManyToMany.add()` fuera de esos servicios debe pasar por una capa de dominio si se requiere blindaje adicional.
- La suite multimedia requiere un directorio temporal con permisos de escritura adecuados en el entorno local.
- No se agregaron relaciones a proyectos, solicitudes o cotizaciones en el portal de clientes porque el alcance se limita al panel administrativo y a preservar el flujo actual.
- La integración futura de documentos contables deberá definir idempotencia, estados de sincronización y reglas de maestro antes de implementar escrituras en Alegra.

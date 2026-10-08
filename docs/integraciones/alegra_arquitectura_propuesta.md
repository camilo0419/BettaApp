# BettaApp — Auditoría y mapeo integral de integración con Alegra

Fecha de análisis: 2026-10-07. Este documento es arquitectónico y no implementa funcionalidades ni modifica modelos, migraciones, interfaces o datos.

## 1. Resumen ejecutivo

BettaApp ya es el sistema operativo comercial: administra el catálogo configurable, cálculo por unidad/m², solicitudes de clientes, cotizaciones, proyectos, producción, estados, comunicaciones y portal de clientes. Alegra debe incorporarse como proveedor externo para facturación, documentos contables y consulta financiera, no como fuente de verdad de la lógica comercial.

La recomendación es una integración asíncrona y desacoplada con tres zonas: datos operativos Betta, staging de datos externos y mapeos/idempotencia. Betta debe poder crear cotizaciones, recibir pedidos, calcular precios y operar producción cuando Alegra esté indisponible. Solo se publican documentos contables después de una acción explícita y auditable.

El diagnóstico anterior registró conexión exitosa a Alegra con HTTP 200 y, dentro del límite de 30 registros por recurso, detectó 30 ítems, 6 categorías de ítems, 3 listas de precios, 2 bodegas, 0 impuestos y 30 contactos cliente. Es una muestra diagnóstica, no un inventario completo. En Betta hay actualmente 5 productos, 7 categorías, 50 campos de producto, 86 opciones, 2 clientes, 0 puntos de venta, 1 contacto, 1 proyecto, 1 solicitud, 2 cotizaciones y 1 ítem de cotización.

Decisión central: Betta administra nombres comerciales, configuración, precios y flujo de producción; Alegra recibe o expone documentos contables y saldos. Ningún documento debe contabilizarse dos veces.

## 2. Inventario de modelos y flujos actuales

### Catálogo y precios

| Modelo | Uso observado | Relaciones/campos relevantes |
|---|---|---|
| `Categoria` | Categoría local del catálogo | `nombre`, `slug`, `orden`, `activa`; 1:N hacia `Producto` |
| `Producto` | Producto publicable/configurable | nombre, descripciones, imagen, activo, destacado, categoría, `tipo_calculo`, `precio_base_m2`, `precio_base_unidad`, revisión |
| `ProductoImagen` | Galería | N:1 con producto, archivos e imágenes |
| `ProductoCampo` | Campos dinámicos por producto | tipos, obligatoriedad, orden, roles de ancho/alto/cantidad |
| `CampoOpcion` | Opciones que alteran el precio | ajuste fijo, por m², por unidad o porcentaje |
| `CampoMaestro`, `CampoMaestroOpcion` | Plantillas reutilizables de campos y opciones | copiado hacia productos; no son variantes contables de Alegra |

El cálculo real está en `DynamicSolicitudForm`: ancho y alto en cm producen `area_m2 = ancho * alto / 10000`; la base usa precio por m² o por unidad y las opciones agregan importes fijos, por m², por unidad o porcentuales. El formulario de ítem de cotización usa precio por unidad o, si falta, precio por m². Esta lógica es exclusiva de Betta y no debe delegarse a Alegra.

### Clientes y relación comercial

| Modelo | Uso |
|---|---|
| `Cliente` | Persona/empresa, identificación, email, teléfonos, dirección, ciudad, notas y estado |
| `ClienteContacto` | Contactos internos del cliente, principalidad y datos de contacto |
| `ClientePuntoVenta` | Puntos de venta asociados al cliente; actualmente no hay registros |
| `ClienteUsuario` | Usuario de portal, permiso `puede_ver_facturacion`, visibilidad de cuenta |
| `Proyecto` | Trabajo agrupador de cliente, contacto, fechas, prioridad y estado |

Las restricciones de modelo evitan emails e identificaciones duplicadas no vacías y garantizan que contactos/proyectos pertenezcan al cliente correcto.

### Ventas, cotizaciones y pedidos

No existe un modelo llamado `Venta`, `Factura`, `Pago` o `Anticipo`.

| Modelo | Flujo actual |
|---|---|
| `Solicitud` | Entrada comercial/pedido; referencia producto, cliente/contacto/proyecto, precio estimado/final, estado de pedido, estado de producción y campos manuales de facturación (`valor_facturado`, `numero_factura`, `fecha_factura`) |
| `SolicitudRespuesta` | Respuestas técnicas del configurador, incluidos archivos |
| `Cotizacion` | Documento comercial local con estados, fechas, cliente, proyecto/solicitud, totales, descuentos, impuesto porcentual, términos y envío PDF |
| `CotizacionItem` | Cantidad, unidad, valor unitario, descuentos, impuesto porcentual y totales calculados |

Una cotización aprobada se marca como convertida y puede copiar su total a `Solicitud.precio_final`; no genera factura ni documento en Alegra. La solicitud tiene estados comerciales, producción y facturación separados, pero el estado de facturación se actualiza manualmente.

### Producción, administración y permisos

- `EmpleadoPerfil`, `SolicitudAsignacion` y `SolicitudTarea` controlan áreas, responsables, prioridades, estados, fechas, evidencias y asignaciones.
- `SolicitudNovedad`, `Notificacion` y `NotificacionCliente` proporcionan trazabilidad operativa y comunicación.
- El panel usa `panel_staff_required`: usuario autenticado, activo y `is_staff`.
- Producción usa `produccion_required`: staff puede supervisar; usuarios no staff necesitan `EmpleadoPerfil` activo.
- Portal cliente usa `cliente_portal_required`, limita por cliente y respeta `puede_ver_toda_la_cuenta` y `puede_ver_facturacion`.
- Django Admin está registrado para casi todos los modelos; no hay permisos granulares por operación de Alegra.
- Servicios existentes: correo (`email_service.py`) y PDF de cotizaciones (`cotizacion_pdf.py`). No existe otra integración REST contable.

## 3. Evidencia de Alegra y capacidades por recurso

La siguiente tabla distingue consulta, escritura documentada y restricciones. Las operaciones de escritura son diseño futuro; no se ejecutaron.

| Recurso | GET | Escritura documentada | Datos/restricciones relevantes |
|---|---|---|---|
| `/items` | listado paginado; filtros por nombre, referencia, categoría, bodega, estado, inventariable y `mode=advanced` | POST, PUT, DELETE documentados para ítems | nombre, descripción, referencia, precio por lista, categoría, impuestos, inventario, tipo `product/service/variantParent/kit`, subitems y variantes; máximo 30 por página |
| `/item-categories` | listado y detalle | POST, PUT, DELETE | dominio de categorías de ítems de Alegra, distinto de `Categoria` local |
| `/contacts` | listado paginado y detalle | POST, PUT, DELETE | contactos pueden ser cliente/proveedor/ambos; modo avanzado incluye lista de precios, vendedor, términos y cuentas |
| `/price-lists` | listado y detalle | POST, PUT, DELETE | tipos por valor o porcentaje; la lista General es base; máximo 30 por página |
| `/estimates` | listado y detalle | POST, PUT, DELETE | cotización requiere cliente, ítems, fecha y vencimiento; puede usar lista, bodega, vendedor y numeración |
| `/invoices` | listado y detalle | POST, PUT, DELETE limitado a borradores | factura requiere cliente e ítems; puede incluir pagos; en Colombia la numeración/documento fiscal es crítica; editar no implica libertad sobre documentos emitidos |
| `/payments` | listado y detalle | POST, PUT, DELETE | ingresos `type=in` o egresos `type=out`; puede asociar facturas; el tipo no se puede cambiar al editar |
| `/credit-notes` | listado y detalle | POST, PUT, DELETE/operaciones específicas según estado | requiere cliente, ítems y fecha; puede asociar facturas, devoluciones, numeración y bodega |
| `/number-templates` | listado y detalle | POST, PUT, DELETE | prefijo, siguiente número, automático, preferido y tipo documental; debe validarse configuración colombiana antes de emitir |

Las consultas de facturas aceptan filtros por estado, cliente, fechas, numeración e ítem; las de pagos permiten `type`, cliente, conciliación y hasta 30 IDs. La API de Alegra documenta que las facturas deben estar asociadas a un contacto y contener al menos un producto o servicio; la creación también exige resolver numeración y datos fiscales aplicables.

Fuentes oficiales: [ítems](https://developer.alegra.com/reference/get_items), [cotizaciones](https://developer.alegra.com/reference/get_estimates), [facturas](https://developer.alegra.com/reference/get_invoices), [crear factura](https://developer.alegra.com/reference/post_invoices), [pagos](https://developer.alegra.com/reference/get_payments-1), [notas crédito](https://developer.alegra.com/reference/post_credit-notes), [numeraciones](https://developer.alegra.com/docs/numeraci%C3%B3n-de-documentos).

## 4. Matriz de correspondencias Betta ↔ Alegra

| Módulo Betta | Recurso Alegra | Equivalencias | Exclusivo Betta | ID externo / maestro | Dirección recomendada | Riesgos y validaciones |
|---|---|---|---|---|---|---|
| Categoría | `/item-categories` | nombre, descripción, estado | slug, orden editorial, relación con catálogo público | `alegra_item_category_id`; Betta maestro editorial | Alegra → staging/Betta; publicación local manual | no emparejar por nombre; estado inactivo no equivale a producto oculto |
| Producto | `/items` | nombre, descripción, referencia, estado, tipo, categoría | imágenes, campos dinámicos, opciones de precio, publicación, cálculo m² | `alegra_item_id`, referencia externa; Betta maestro comercial | Alegra → staging; Betta → publicación local; escrituras posteriores explícitas | variantes/kits requieren decisión; no sobrescribir producto existente |
| Precio | `/price-lists` y `item.price` | moneda, precio base, lista | m², unidad, ajustes por opción, precio final de cotización | ID de lista y snapshot de precio | Betta maestro de precio; Alegra solo documento emitido | redondeo, impuestos incluidos/excluidos y cambios de lista |
| Cliente | `/contacts` | nombre, identificación, email, teléfono, dirección, estado | portal, permisos, proyectos, notas operativas | `alegra_contact_id`; clave de conciliación identificador normalizado | importación Alegra → staging; Betta administra relación local | identificaciones ausentes/duplicadas; persona vs empresa |
| Contacto/punto de venta | `internalContacts` / contacto | nombre, email, teléfono, dirección parcial | semántica de punto de venta y portal | ID externo específico si existe | no sincronizar automáticamente hasta confirmar semántica | no confundir contacto interno con sucursal/punto de venta |
| Cotización | `/estimates` | cliente, fecha, vencimiento, ítems, precio, descuento, impuesto | configurador, detalle técnico, estados de diseño/envío, PDF propio | `alegra_estimate_id` opcional; `betta_cotizacion_id` como referencia | Betta → Alegra solo si se decide usar cotización externa; lectura separada | crear dos cotizaciones para la misma oportunidad; no convertir automáticamente |
| Pedido | no hay equivalente directo | cliente/ítems podrían alimentar factura | solicitud, producción, archivos, novedades | `betta_solicitud_id` en referencia/metadata disponible | Betta maestro; no sincronizar como factura | pedido no es documento contable |
| Venta/factura | `/invoices` | cliente, ítems, cantidades, precios, impuestos, fechas | lógica comercial, producción y aprobación | `alegra_invoice_id`, número completo, plantilla | Betta origina solicitud; Alegra maestro del documento fiscal y estado contable | duplicidad, emisión irreversible, numeración y estado |
| Recaudo/anticipo | `/payments` | cliente, fecha, monto, cuenta, método, factura | anticipo comercial local aún inexistente | `alegra_payment_id`; relación a factura | Alegra → Betta para cartera; Betta no debe inventar recaudos | pago sin factura/categoría; doble aplicación; tipo in/out |
| Nota crédito | `/credit-notes` | factura, cliente, ítems, monto, fecha, devoluciones | motivo operativo y aprobación interna | `alegra_credit_note_id` | Betta solicita/aprueba; Alegra emite; luego consulta estado | afectar saldo sin documento; asociación parcial de facturas |
| Numeración fiscal | `/number-templates` | prefijo, próximo número, tipo, automático | reglas internas de aprobación | `alegra_number_template_id` | Alegra maestro | no reservar números en Betta; concurrencia y documento equivalente POS |
| Bodega/inventario | `/warehouses`, campos de `/items` | bodega, unidad, cantidades | no implementar inventarios | IDs externos solo para futura referencia | solo consulta futura | no crear stock local ni movimientos en esta fase |

Regla transversal: cualquier sincronización debe guardar la pareja `(sistema, recurso, external_id)` con unicidad, estado, hash de payload normalizado, última lectura y última escritura; nunca usar el nombre como identidad.

## 5. Diseño propuesto de modelos nuevos

No se crean en esta fase. Como diseño futuro, preferir tablas desacopladas sobre modificar `Producto`, `Cliente` o `Cotizacion` inicialmente:

1. `ExternalSystem`: proveedor, ambiente, configuración no secreta y estado.
2. `ExternalObjectMap`: sistema, tipo de recurso, `external_id`, content type/ID local, dirección de autoridad, estado, timestamps, hash y unique constraint por sistema/recurso/ID.
3. `AlegraItemStaging`, `AlegraContactStaging`, `AlegraCategoryStaging`, `AlegraPriceListStaging`: payload depurado, versión, fecha de consulta, estado `new/reviewed/accepted/rejected`, errores de validación y decisión de publicación.
4. `IntegrationEvent`/`OutboxEvent`: evento de dominio, clave idempotente, payload, intentos, próximo reintento, respuesta resumida y estado.
5. `ExternalDocumentLink`: relación explícita entre `Solicitud`/`Cotizacion` y documento Alegra, tipo, número, estado, total y hash de origen.
6. `SyncAuditLog`: recurso, operación, dirección, actor, correlación, resultado y referencia segura al error; nunca token ni datos personales completos.

La tabla de staging sí es conveniente para productos importados: permite revisar y publicar sin tocar productos existentes. La tabla de mapeos es obligatoria para evitar duplicación y permitir reintentos idempotentes.

## 6. Estrategia de importación y publicación de productos

1. Consultar `/items?mode=advanced` de forma paginada y guardar solo payload sanitizado en staging.
2. Normalizar referencia, nombre, categoría, tipo, estado y listas de precio.
3. Buscar coincidencias existentes por referencia validada; como segunda señal usar nombre normalizado + categoría, nunca crear automáticamente por nombre ambiguo.
4. Marcar `match`, `new`, `conflict` o `ignored` y exigir revisión para conflictos.
5. Para `new`, crear un producto Betta solo en estado no publicable/inactivo cuando exista autorización de importación; no sobrescribir productos actuales.
6. No copiar automáticamente variantes/kits a `ProductoCampo`: son conceptos diferentes. Requieren decisión de modelado posterior.
7. La publicación local sigue siendo decisión de Betta; un ítem importado no aparece en tienda hasta revisión de categoría, precio, imágenes, campos y reglas de cálculo.
8. Mantener `alegra_item_id` fuera del modelo actual inicialmente mediante mapeo externo.

## 7. Estrategia de precios

Betta debe ser el sistema maestro del precio comercial mostrado y cotizado. La política principal propuesta es:

- `precio_base_unidad` y `precio_base_m2` continúan siendo las bases comerciales.
- Los extras de `CampoOpcion` conservan sus reglas fija, por m², por unidad y porcentaje.
- `CotizacionItem.valor_unitario` y sus totales son snapshot de la decisión comercial al cotizar; no deben recalcularse desde Alegra después de enviada/aprobada.
- Las listas de Alegra se consultan para contexto o para preparar documentos, pero no reemplazan la fórmula local.
- Al facturar, enviar a Alegra el precio final aprobado, sin impuestos ni descuentos duplicados, y declarar explícitamente el tratamiento del impuesto.
- Si se requiere una única política principal, usar “precio Betta aprobado por cotización” como fuente; la lista General de Alegra queda como dato contable/operativo externo, no como autoridad de la tienda.

## 8. Arquitectura de facturación

Flujo recomendado:

`Solicitud aprobada → Cotizacion convertida → aprobación de facturación → outbox idempotente → crear/consultar contacto → validar item/cliente/impuesto/numeración → crear factura Alegra → guardar vínculo → consultar estado → actualizar solo campos contables Betta`.

Reglas:

- Una solicitud puede tener como máximo un documento fiscal activo por tipo, salvo sustitución/nota crédito explícita.
- Antes de POST a `/invoices`, buscar un `ExternalDocumentLink` o evento con la misma clave idempotente.
- No crear factura automáticamente al marcar producción terminada.
- El número de factura, estado fiscal, saldo y fecha de emisión son maestros de Alegra una vez emitido.
- Betta conserva el vínculo con pedido/cotización/producción y muestra el resultado; no duplica la venta en otro modelo local.
- Para Colombia validar plantilla de numeración y tipo documental aplicable, incluyendo documento equivalente POS cuando corresponda. La numeración preferida no debe asumirse sin consultar configuración.
- Pagos incluidos en la creación de una factura deben ser una decisión separada; evitar registrar pago en Alegra y luego volver a importar el mismo recaudo como anticipo local.

## 9. Cartera, recaudos y módulos administrativos futuros

Actualmente no hay cartera ni pagos/anticipos. El diseño futuro debe separar:

- `AccountsReceivableSnapshot`: saldos y vencimientos consultados desde facturas Alegra.
- `PaymentAllocation`: pago Alegra, factura(s), monto aplicado, fecha y diferencias.
- `Advance`: anticipo comercial Betta antes de documento Alegra, con estado y posterior aplicación; no llamarlo “pago Alegra” hasta existir el recurso externo.
- `CreditNoteCase`: solicitud/motivo/aprobación Betta enlazada a la nota crédito emitida.
- Reportes administrativos: fuentes diferenciadas “operación Betta” y “contabilidad Alegra”, con fecha de corte y estado de sincronización.

Un reporte de ventas debe declarar si cuenta solicitudes/cotizaciones o facturas emitidas. Un reporte de recaudo debe usar pagos asociados a facturas y no sumar nuevamente el total de la factura. La conciliación debe ser por IDs externos, no por número visible solamente.

## 10. Errores, auditoría e idempotencia

- Estados de integración: `pending`, `processing`, `succeeded`, `retryable`, `failed`, `manual_review`.
- Reintentar solo timeouts, red, 429 y 5xx; respetar `Retry-After` y aplicar backoff con jitter.
- No reintentar automáticamente 400/401/403/409 sin corregir datos o permisos.
- Registrar endpoint, método, status, duración, correlación, recurso y error depurado; excluir tokens, Authorization y PII completa.
- Usar clave idempotente determinista: `betta:{modelo}:{id}:invoice:{version}` o equivalente.
- Congelar snapshots de cotización y factura enviados para poder explicar diferencias.
- Circuit breaker y cola permiten que Betta continúe operando si Alegra está caída.
- Un webhook futuro debe validar autenticidad, deduplicar evento, responder rápido y procesar asíncronamente; el polling será reconciliación, no segunda creación.

## 11. SQLite, producción, migraciones y respaldo

### Estado observado

- Configuración local efectiva por defecto: SQLite en `db.sqlite3`.
- Producción documentada en README: Python 3.12, virtualenv, WSGI/Passenger y comandos Django; se menciona PythonAnywhere.
- `PyMySQL` está en `requirements.txt` y `settings.py` acepta `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST` y `DB_PORT`, incluyendo `utf8mb4` para MySQL.
- La documentación de cPanel existente cubre principalmente publicación de media; no define un procedimiento completo de base de datos, backups, rollback, cron, workers o secretos.
- Hay 18 migraciones Django de `0001` a `0018`; no se ejecutaron ni se generaron migraciones en esta auditoría.

### Compatibilidad y despliegue seguro futuro

- Probar migraciones en una copia de SQLite y en MySQL compatible antes de producción; revisar longitudes, índices, `JSON`, fechas con zona horaria y restricciones únicas.
- No depender de SQLite para workers concurrentes, outbox de alta frecuencia o integración de producción; usar MySQL/MariaDB administrado si el hosting lo soporta.
- Respaldar base de datos y `MEDIA_ROOT` antes de cada migración; probar restauración, no solo existencia del archivo.
- Usar entorno virtual separado, variables de entorno fuera del repositorio, `DEBUG=False`, `SECRET_KEY` real, `ALLOWED_HOSTS`, HTTPS y `collectstatic` controlado.
- Desplegar en ventana controlada: backup, mantenimiento breve si aplica, migraciones, `check --deploy`, smoke tests, reload WSGI y rollback documentado.
- No conectar esta auditoría a producción ni inferir que el README sustituye la configuración real del hosting.

## 12. Plan de implementación por fases

| Fase | Alcance | Salida |
|---|---|---|
| 0 | contrato funcional y fiscal Colombia | decisiones sobre maestro, numeración, impuestos, documento POS, responsables |
| 1 | staging y mapeos externos | modelos desacoplados, importación controlada, revisión manual |
| 2 | catálogo y clientes de solo lectura | reconciliación, búsqueda de duplicados, publicación manual |
| 3 | outbox, auditoría e idempotencia | infraestructura segura sin emitir documentos todavía |
| 4 | cotizaciones opcionales | decidir si Alegra replica cotizaciones o solo consulta; evitar doble operación |
| 5 | facturación Betta → Alegra | una factura por operación, numeración validada, vínculo y reintentos |
| 6 | pagos/cartera/nota crédito | conciliación, saldos, anticipos y flujos aprobados |
| 7 | webhooks y reportes | sincronización incremental, dashboards con fuentes separadas |

## Decisiones pendientes

1. ¿Betta enviará cotizaciones a Alegra o solo facturas?
2. ¿Quién aprueba y desde qué estado se puede emitir una factura?
3. ¿Qué numeración colombiana se utilizará para factura y documento equivalente POS?
4. ¿Alegra tendrá impuestos configurados o se bloqueará facturación hasta que existan?
5. ¿Cómo se representa una variante o kit en el catálogo comercial de Betta?
6. ¿Se requiere inventario futuro? Esta propuesta no lo implementa.
7. ¿Qué campos son maestros de Betta y cuáles se aceptan desde Alegra para clientes?
8. ¿Cómo se manejarán anticipos antes de la factura y pagos sin factura?
9. ¿Qué proveedor/plan de hosting y motor MySQL estarán realmente disponibles en producción?
10. ¿Qué política de respaldo, retención de auditoría y recuperación se aprobará?

## Referencias oficiales

- https://developer.alegra.com/reference/get_items
- https://developer.alegra.com/reference/get_item-categories
- https://developer.alegra.com/reference/listcontacts-1
- https://developer.alegra.com/reference/get_price-lists
- https://developer.alegra.com/reference/get_estimates
- https://developer.alegra.com/reference/get_invoices
- https://developer.alegra.com/reference/post_invoices
- https://developer.alegra.com/reference/get_payments-1
- https://developer.alegra.com/reference/post_payments-1
- https://developer.alegra.com/reference/post_credit-notes
- https://developer.alegra.com/docs/numeraci%C3%B3n-de-documentos

## Implementación Fase 1

La Fase 1 implementa únicamente infraestructura local y consulta de ítems:

- `ExternalSystem`, `ExternalObjectMap`, `AlegraItemStaging` y `SyncAuditLog` están desacoplados de la lógica comercial.
- El servicio de importación usa el cliente Alegra existente, consulta `/items?mode=advanced` por lotes y hace upsert por `(sistema, recurso, ID externo)`.
- Las coincidencias se detectan como revisión; los conflictos por nombre no se resuelven automáticamente.
- La importación exige categoría local y tipo de cálculo elegidos por un usuario autorizado, crea el producto con `activo=False`, precios en cero y `requiere_revision=True`.
- No se importan variantes, campos dinámicos, inventario ni listas de precios comerciales.
- Todas las acciones del panel que modifican Betta son `POST` con CSRF y permisos de servidor; no existen rutas de escritura a Alegra.
- Las rutas y el procedimiento operativo están documentados en `docs/integraciones/alegra_fase1_implementacion.md`.

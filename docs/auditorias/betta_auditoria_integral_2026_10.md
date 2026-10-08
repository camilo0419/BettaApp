# Auditoría integral previa a estabilización — BettaApp

**Fecha:** 2026-10-07  
**Modo:** exclusivamente lectura. No se modificó código funcional, no se aplicaron migraciones, no se modificó SQLite, no se usó producción y no se hicieron llamadas a Alegra.

## 1. Resumen ejecutivo

BettaApp es una aplicación Django 5.2 con una aplicación principal (`tienda`), SQLite local y configuración parametrizable para MySQL mediante variables de entorno. El sistema cubre catálogo configurable, clientes, puntos de venta, proyectos, solicitudes, cotizaciones, ventas, producción, cartera e integración de consulta con Alegra.

La separación entre operación Betta, staging externo, mapeos y auditoría es aprovechable. La inspección estática confirma que `AlegraReadOnlyClient` solo construye solicitudes GET y que los servicios revisados no contienen POST, PUT, PATCH ni DELETE hacia Alegra.

No obstante, el sistema no debe considerarse estable para uso financiero o productivo sin correcciones. Se confirmaron riesgos importantes en cálculo de cartera, informes de facturación, vinculación factura-venta, cardinalidad de mapeos externos, validaciones que pueden omitirse por ORM, frescura de staging y endurecimiento de producción.

### Veredicto técnico

**NO APTO HASTA CORREGIR HALLAZGOS CRÍTICOS.**

Puede continuar en desarrollo local controlado, pero no se recomienda usar sus indicadores de cartera/facturación como cifras oficiales ni iniciar escrituras hacia Alegra o desplegar antes de completar P0.

## 2. Inventario real del sistema

### Estructura

- `config`: settings, URLs raíz y WSGI.
- `tienda`: única aplicación registrada en `INSTALLED_APPS`.
- `tienda/models.py`: 1.893 líneas; modelos comerciales, producción, integración y notificaciones.
- `tienda/views.py`: 4.298 líneas; panel, portal, producción, Alegra y ventas concentrados en un módulo grande.
- `tienda/forms.py`: 1.499 líneas de formularios y validaciones.
- `tienda/urls.py`: 155 líneas de rutas públicas, portal, panel, producción e integración.
- `tienda/services`: 10 módulos, incluidos Alegra, cartera, PDF, correo y centro de control.
- `tienda/migrations`: 25 migraciones; `showmigrations tienda` reportó 0001–0025 aplicadas localmente.
- Pruebas: suite general de 132 casos durante esta auditoría.

### Módulos

| Módulo | Componentes | Estado real |
|---|---|---|
| Catálogo | `Categoria`, `Producto`, campos, opciones e imágenes | Implementado |
| CRM | `Cliente`, contactos, puntos de venta y usuarios portal | Implementado |
| Proyectos | `Proyecto`, M2M de puntos de venta, tareas y novedades | Implementado; integridad M2M incompleta fuera de formularios |
| Comercial | `Solicitud`, `Cotizacion`, items, `Venta`, items | Implementado; conversión probada |
| Producción | estados, asignaciones, tareas, novedades y panel | Implementado |
| Alegra | cliente GET, staging de items/contactos/facturas/pagos, mapeos y auditoría | Implementado con mocks; API real no validada |
| Cartera | saldos, pagos staging, aging, gestiones, compromisos y responsables | Implementado; riesgos financieros confirmados |
| Centro de control | indicadores y alertas manuales | Implementado; sin scheduler |
| Despliegue | SQLite local, MySQL configurable, Passenger/WSGI y docs cPanel | Parcial; producción no verificable |

Dependencias declaradas: Django, python-dotenv, PyMySQL, WhiteNoise, Pillow y reportlab. No se instalaron dependencias.

## 3. Arquitectura actual

```text
Cliente → Solicitud → Cotización → Venta → Producción → Entrega
   │          │           │          │
   └──── Proyecto / puntos de venta ─┘

Alegra GET → staging → conciliación manual → mapeos/auditoría → cartera conocida
```

Betta es el sistema maestro de productos, precios, clientes, solicitudes, cotizaciones, ventas y producción. Alegra es fuente externa de facturas/pagos consultados.

## 4. Modelos, relaciones e integridad

Fortalezas confirmadas:

- `ClientePuntoVenta.cliente` es obligatorio; hay unicidad `(cliente, nombre)` y un único punto principal activo (`models.py:500-541`).
- `Proyecto.cliente` es FK opcional y `Proyecto.puntos_venta` es M2M (`models.py:650-652`).
- Solicitud, cotización y venta tienen validaciones de pertenencia para cliente, proyecto, punto, contacto y documentos relacionados.
- VentaItem y CotizacionItem conservan snapshots de descripción, cantidades e importes.
- Staging de items, contactos, facturas y pagos tiene unicidad `(system, external_id)` (`models.py:1180`, `1231`, `1786`, `1851`).
- La conversión de cotización usa `transaction.atomic()` y `select_for_update()` (`views.py:1906-1918`); las pruebas cubren idempotencia.

Deficiencias:

- `Proyecto.clean()` valida puntos de venta solo si el M2M ya está persistido (`models.py:691-700`). El formulario valida la selección, pero `puntos_venta.add(...)` por ORM puede vincular una sede de otro cliente.
- `Venta.clean()`, `Solicitud.clean()`, `Cotizacion.clean()`, `CarteraGestion.clean()` y `CompromisoPago.clean()` no son restricciones de base de datos. En varios modelos `save()` no llama `full_clean()`; un servicio u ORM directo puede omitir reglas de formularios.
- `ExternalObjectMap` solo impone unicidad del lado externo `(system, resource_type, external_id)` (`models.py:1701-1708`). No impide varios IDs Alegra activos para el mismo objeto local.
- `VentaFacturaAlegra` solo impide duplicar el par venta-factura (`models.py:1187-1195`); no valida cliente común ni unicidad de factura por venta según una política explícita.
- Consecutivos de cotización y venta se calculan leyendo el último ID (`models.py:917-925`, `1077-1085`), menos robusto ante concurrencia.

## 5. Flujo comercial

Implementado: solicitudes, proyectos, cotizaciones, conversión a ventas, snapshots históricos de venta, estados de producción, tareas y entrega. Los formularios filtran relaciones por cliente y las vistas principales usan POST/CSRF.

Riesgos: la conversión es idempotente en la vista actual, pero no hay restricción única sobre `Venta.cotizacion_id`; la protección fuera de esa vista depende del servicio. La venta directa queda validada por `VentaForm`, pero el modelo permite guardado ORM sin `full_clean()`. Proyecto, solicitud y punto de venta pueden quedar inconsistentes si se usan APIs internas sin formularios.

## 6. Auditoría de Alegra

`alegra_client.py:43-115`:

- credenciales desde entorno;
- Basic Auth en memoria;
- timeout configurable, 15 s por defecto;
- `Request(..., method="GET")`;
- errores 401/403/429 y conexión;
- paginación limitada a 30.

La inspección de `tienda/services/alegra_*.py` confirmó ausencia de métodos HTTP de escritura. No hubo llamadas reales porque `ALEGRA_EMAIL`, `ALEGRA_API_TOKEN` y `ALEGRA_BASE_URL` no están configuradas en el entorno.

Idempotencia y staging están implementados por `(system, external_id)`, con transacciones para facturas/pagos y hashes de payload. Riesgos observados:

- Facturas y pagos guardan `original_data` completo (`alegra_invoice_import.py:143`, `alegra_payment_import.py:94`; `models.py:1167`, `1218`), con posible PII innecesaria y retención indefinida.
- Los conciliadores buscan candidatos recorriendo objetos Python (`alegra_import.py:341-348`, `alegra_contact_import.py:206-213`, `alegra_invoice_import.py:183-194`, `alegra_payment_import.py:115-127`).
- JSON inválido o estructuras inesperadas pueden escapar de la captura de `AlegraError`.
- Una sincronización limitada se registra como éxito sin una garantía de cobertura histórica completa.
- Estados, impuestos, moneda, saldo y distribución de pagos no fueron validados con una respuesta real de esta cuenta.

## 7. Auditoría financiera

La cartera toma como fuente prioritaria `AlegraInvoiceStaging.balance`, lo cual es conceptualmente correcto, y excluye `void` en `invoice_cartera_row()` (`services/cartera.py:43-58`). Hay tres riesgos críticos:

1. `services/cartera.py:80-83` suma todo `STATUS_PARTIAL` a `overdue_balance` y `overdue_count`, aunque la fecha de vencimiento sea futura. Una factura parcial no vencida se presenta como vencida.
2. `invoice_cartera_row()` excluye solo `void`; `draft` y `unknown` con saldo pueden entrar en cartera.
3. `ventas_informes()` suma todos los `AlegraInvoiceStaging.total` y cuenta todos los staging (`views.py:2039-2051`), sin excluir anuladas/borradores/desconocidas ni indicar cobertura de sincronización.

El saldo actual se usa para aging, pero no hay snapshots financieros. No puede presentarse como saldo histórico a una fecha anterior. Los pagos solo se almacenan como staging de ingresos y no se convierten en ingresos Betta; no se observó código que convierta notas crédito en pagos. La distribución de un pago a varias facturas se conserva como lista de IDs, no como asignaciones locales verificables.

## 8. Seguridad

Controles positivos:

- Panel y Alegra requieren autenticación/staff; operaciones mutables principales usan POST y middleware CSRF.
- Producción usa `usuario_puede_ver_solicitud/tarea/proyecto`.
- `.env` no está versionado y el escaneo no encontró valores de secretos en código versionado.

Hallazgos:

- `alegra_factura_vincular_venta()` (`views.py:2173-2186`) solo exige evidencia textual; no valida que factura y venta pertenezcan al mismo cliente. Es un riesgo de integridad financiera e IDOR horizontal.
- `cartera_gestion_crear()` y `cartera_compromiso_crear()` (`views.py:2083-2105`) no llevan `@require_POST`; solo guardan si llega POST, pero el contrato HTTP queda ambiguo.
- `cartera_asignar_responsable()` (`views.py:2107-2117`) acepta cualquier usuario existente sin política explícita de usuario activo/rol de cartera.
- La configuración ejecutada mostró `DEBUG=True`, `SECURE_SSL_REDIRECT=False`, `SESSION_COOKIE_SECURE=False` y `CSRF_COOKIE_SECURE=False`. `check --deploy` reportó W004, W008, W009, W012, W016 y W018. Es evidencia del entorno local; producción sigue no verificable.
- La autorización distingue staff y algunos permisos de Alegra, pero no existe una matriz fina para separar comercial, cartera, integración y producción.

## 9. Centro de control

`centro_control_snapshot()` (`services/centro_control.py:49-151`) solo lee datos locales. Las alertas manuales usan POST/CSRF (`views.py:1417-1422`) y las pruebas cubren permisos/idempotencia.

Riesgos:

- Solo se advierte staging vacío o sin fecha (`centro_control.py:91-96`); no se advierte que la última sincronización sea antigua.
- `_notify_once()` usa `exists()` seguido de `create()` (`centro_control.py:155-177`) sin restricción única: dos procesos concurrentes pueden duplicar alertas.
- No hay estado de alerta/resolución; si cambia la condición, una notificación no leída puede quedar obsoleta.
- No se generan alertas de ventas pendientes porque no existe una fecha de seguimiento confiable; la cobertura queda parcial por diseño prudente.

## 10. Interfaz, navegación y rendimiento

El menú incluye Dashboard, Centro de control, Solicitudes, Cotizaciones, Ventas, Cartera, Clientes, Proyectos, Alegra, Productos y Producción. Las acciones secundarias se alcanzan desde los detalles. Hay responsive CSS, tablas con scroll y estados de carga, pero no existe auditoría automatizada de accesibilidad ARIA/foco/teclado.

`cartera_dashboard` filtra las filas por estado/aging, pero calcula `summary` sobre el queryset base (`views.py:2058-2071`); los KPIs pueden no corresponder al filtro visible. `cartera_rows()` materializa toda la cartera (`services/cartera.py:64-67`). Los conciliadores recorren catálogos en Python. Con miles de registros hay riesgo de latencia, memoria y bloqueo de SQLite.

La documentación cPanel cubre principalmente media files; no existe un procedimiento completo verificable de backup, rollback, migraciones, cron, workers, secretos y smoke tests. README también contiene instrucciones históricas de PythonAnywhere.

## 11. Estado de pruebas

| Comando | Resultado |
|---|---|
| `python manage.py check` | Correcto, sin problemas |
| `python manage.py check --deploy` | 6 advertencias de seguridad |
| `python manage.py makemigrations --check --dry-run` | `No changes detected` |
| `python manage.py showmigrations tienda` | 0001–0025 aplicadas localmente |
| `python manage.py test --no-color -v 1` | 132 pruebas: 130 correctas, 2 errores `PermissionError` |

Hay pruebas con mocks/fakes para Alegra, importación, idempotencia, ventas, cartera, relaciones y centro de control. Faltan casos específicos para parcial no vencida, draft/unknown, clientes cruzados en vínculos, cardinalidad del mapeo local, concurrencia, JSON inválido, respuestas parciales, frescura de staging y filtros de KPIs.

### PermissionError confirmado

1. `tienda.tests.PortalClienteTests.test_panel_producto_editar_uploads_image_without_invalid_storage_error`, `tienda/tests.py:765-766`: falla al crear `productos` bajo `tempfile.TemporaryDirectory()` y también al limpiar.
2. `tienda.tests.MediaStorageTests.test_safe_media_url_returns_empty_for_missing_local_file`, `tienda/tests.py:70`: falla durante la limpieza de otro directorio temporal.

Ambos apuntan a ACL/permisos del sandbox Windows bajo `AppData\Local\Packages\sandbox...\AC\Temp`. No se corrigieron. Es una causa probable de entorno, no una prueba suficiente para descartarlo en CI/producción.

## 12. Matriz de estado

| Módulo | Implementado | Pruebas | API real | Riesgo | Recomendación |
|---|---:|---:|---:|---|---|
| Catálogo/precios | Sí | Parcial | No | variantes y búsquedas lineales | P2 |
| Clientes/puntos | Sí | Sí | No | M2M/ORM puede romper pertenencia | P1 |
| Proyectos/solicitudes | Sí | Sí | No aplica | invariantes dependientes de formulario | P1 |
| Cotizaciones/ventas | Sí | Sí | No aplica | vínculos e informes financieros | P0/P1 |
| Producción | Sí | Parcial | No aplica | fechas/permiso por responsable | P2 |
| Facturas/pagos Alegra | Sí, staging | Mocks | No | cobertura y estados externos | P0/P4 |
| Cartera | Sí | Básica | No | aging y estados financieros | P0 |
| Centro de control | Sí | Específica | No | frescura y resolución | P1/P2 |
| Seguridad/despliegue | Parcial | `check` | No | warnings de producción | P0/P4 |

## 13. Hallazgos clasificados

### F-001 — CRÍTICO — Parciales no vencidas contadas como vencidas

- **Evidencia:** `services/cartera.py:75-83` incluye todo `STATUS_PARTIAL` en saldo vencido.
- **Reproducción:** factura total 100, saldo 40 y vencimiento futuro.
- **Impacto:** cartera vencida y porcentaje inflados.
- **Corrección recomendada:** separar parcialidad de vencimiento y usar `due_date` para el bucket.
- **Pruebas:** parcial futura, del día, vencida y pagada.

### F-002 — CRÍTICO — Facturación suma staging no reportable

- **Evidencia:** `views.py:2039-2051` suma todos los totales de factura.
- **Reproducción:** staging `void`, `draft` o `unknown` con total positivo.
- **Impacto:** cifras de facturación incorrectas.
- **Corrección recomendada:** estados reportables, cobertura, corte y exclusión explícita.
- **Pruebas:** todos los estados y sincronización limitada.

### F-003 — ALTO — Vínculo factura-venta entre clientes permitido

- **Evidencia:** `views.py:2173-2186` valida solo evidencia textual.
- **Impacto:** conciliación falsa y mezcla de información.
- **Corrección:** validar cliente externo/local y conflictos con permiso elevado.

### F-004 — ALTO — Mapeo externo sin unicidad local

- **Evidencia:** `models.py:1701-1708`; no hay unique constraint del objeto local.
- **Impacto:** varios IDs Alegra para un producto/cliente, duplicación lógica.
- **Corrección:** definir cardinalidad y restricción/servicio correspondiente.

### F-005 — ALTO — Invariantes dependientes de formularios

- **Evidencia:** `Proyecto.clean()` en `models.py:691-700`; varios `save()` no llaman `full_clean()`.
- **Reproducción:** ORM directo con sede de otro cliente, o venta con proyecto ajeno.
- **Impacto:** datos inconsistentes.
- **Corrección:** servicios de dominio y restricciones donde sean posibles.

### F-006 — ALTO — Sin advertencia de staging financiero antiguo

- **Evidencia:** `centro_control.py:91-96` solo detecta ausencia o fecha nula.
- **Impacto:** saldo actual antiguo puede verse como vigente.
- **Corrección:** política de frescura, fecha de corte y cobertura visible.

### F-007 — ALTO — Configuración ejecutada no endurecida

- **Evidencia:** `settings.py:50`, `165-167`; `check --deploy` reportó seis advertencias.
- **Impacto:** si se replica en producción, riesgo de DEBUG, cookies y transporte.
- **Corrección:** configuración explícita de HTTPS, HSTS, cookies, clave y hosts.

### F-008 — MEDIO/ALTO — Payloads externos completos retienen PII

- **Evidencia:** `alegra_invoice_import.py:143`, `alegra_payment_import.py:94`, `models.py:1167`, `1218`.
- **Impacto:** mayor superficie de datos personales/financieros y backups más sensibles.
- **Corrección:** allow-list, retención, redacción y permisos.

### F-009 — MEDIO — Conciliación y cartera escalan por barridos Python

- **Evidencia:** conciliadores en `alegra_import.py:341-348`, `alegra_contact_import.py:206-213`, `alegra_invoice_import.py:183-194`, `alegra_payment_import.py:115-127`; cartera materializada en `cartera.py:64-67`.
- **Impacto:** latencia, memoria y bloqueo SQLite con volumen.
- **Corrección:** índices, consultas normalizadas, paginación y procesos fuera del request.

### F-010 — MEDIO — Alertas sin resolución ni unicidad fuerte

- **Evidencia:** `centro_control.py:155-177` usa `exists()` + `create()`; no hay constraint ni estado de alerta.
- **Impacto:** duplicados concurrentes y notificaciones obsoletas.
- **Corrección:** identidad única, estado abierto/resuelto y actualización idempotente.

Hallazgos adicionales: KPIs de cartera no respetan todos los filtros visibles (`views.py:2058-2071`), dos endpoints de cartera no declaran `@require_POST`, no existe procedimiento completo verificable de despliegue cPanel y varios modelos financieros no están registrados en `admin.py`.

## 14. Plan de estabilización

### P0 — correcciones críticas — complejidad media/alta

1. Corregir aging y clasificación de parciales.
2. Excluir estados no reportables de informes y declarar cobertura/fecha de corte.
3. Bloquear vínculos factura-venta entre clientes distintos.
4. Definir cardinalidad de mapeos externos.
5. Validar configuración de producción con `check --deploy` y secretos reales del hosting.

### P1 — correcciones altas — complejidad media

1. Reforzar invariantes en servicios/ORM.
2. Añadir frescura y cobertura de staging financiero.
3. Limitar payloads PII y revisar acceso a admin/backups.
4. Definir permisos por rol para cartera, integración, comercial y producción.

### P2 — integridad y pruebas — complejidad media

Añadir pruebas para estados draft/unknown, parciales futuras, clientes cruzados, ORM directo, concurrencia, JSON inválido, respuestas parciales, mapeos duplicados, alertas resueltas y KPIs filtrados.

### P3 — rendimiento/UX — complejidad media/alta

Sustituir barridos Python, paginar agregaciones, mover sincronizaciones grandes fuera de peticiones web y auditar accesibilidad/foco/teclado.

### P4 — extremo a extremo — complejidad alta

Validar backup/restauración, migraciones en MySQL/MariaDB equivalente, WSGI/media/HTTPS/permisos, smoke tests y flujo comercial completo sin escritura a Alegra.

## 15. Riesgos pendientes de validación real

- Respuestas reales de Alegra Colombia: estados, saldos, impuestos, moneda y distribución de pagos.
- Rate limits, latencia y paginación con volumen real.
- Compatibilidad efectiva con MySQL/MariaDB del hosting.
- ACL de media, backups, logs y secretos en cPanel.
- Cardinalidad contable factura-venta y tratamiento de anulaciones/notas crédito.

## 16. Veredicto

**NO APTO HASTA CORREGIR HALLAZGOS CRÍTICOS.**

La arquitectura es aprovechable, pero la siguiente fase debe ser corrección P0 y validación financiera antes de facturación electrónica, automatización programada o despliegue.

## 17. Actualización P1-B — vigencia y seguridad por entorno

**Fecha de actualización:** 2026-10-08

### F-006 — Estado

Corregido en desarrollo local. Se creó `tienda/services/sync_freshness.py`, que diferencia `never`, `current`, `stale`, `partial` y `failed` por recurso. Facturas y pagos se evalúan de forma independiente mediante `SyncAuditLog`; una sincronización parcial no se registra como éxito completo y no actualiza artificialmente la fecha de éxito de otra fuente.

El umbral predeterminado es `ALEGRA_FINANCIAL_FRESHNESS_MINUTES=60`, configurable por entorno. Las advertencias se muestran en cartera, informes financieros, centro de control, detalle financiero del cliente, catálogo y detalle de facturas Alegra. Los datos históricos se conservan y no se bloquea su consulta, pero se identifican como no definitivos cuando la fuente no está verificada.

Los importadores de facturas, pagos, clientes y productos registran `metadata.complete`; si el límite impide demostrar cobertura completa, el resultado es `partial`. No se hicieron llamadas reales a Alegra durante esta corrección.

### F-007 — Estado

Corregido parcialmente mediante configuración explícita por entorno en `config/settings.py`:

- `DJANGO_ENV=development` mantiene funcional el acceso HTTP local y no activa redirección HTTPS, cookies seguras ni HSTS por defecto.
- `DJANGO_ENV=production` exige `DJANGO_DEBUG=False` y activa por defecto redirección HTTPS, cookies seguras, HSTS y `SECURE_CONTENT_TYPE_NOSNIFF`; los valores pueden ajustarse explícitamente por variables de entorno.
- `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` y `DJANGO_CSRF_TRUSTED_ORIGINS` permanecen externos al código.
- `SECURE_PROXY_SSL_HEADER` solo se configura si se proporciona explícitamente `DJANGO_SECURE_PROXY_SSL_HEADER`; no se asumió una configuración de proxy de cPanel.

La configuración efectiva de cPanel, certificados, proxy, dominios, permisos de media y secretos de producción sigue sin estar verificada desde este entorno. Por tanto, P1-B no certifica la seguridad efectiva de producción.

### Validación P1-B

- `python manage.py check`: correcto.
- `python manage.py makemigrations --check --dry-run`: sin cambios pendientes.
- `python manage.py check --deploy` en el entorno local: 7 advertencias: `security.W004` (HSTS no activo), `security.W006` (`SECURE_CONTENT_TYPE_NOSNIFF` desactivado), `security.W008` (redirección SSL desactivada), `security.W009` (SECRET_KEY local débil), `security.W012` (cookie de sesión no segura), `security.W016` (cookie CSRF no segura) y `security.W018` (`DEBUG=True`). Son esperables en desarrollo y no prueban por sí mismas el estado de cPanel.
- `python manage.py check --deploy` con configuración aislada de producción (`DJANGO_ENV=production`, clave y hosts de prueba): sin advertencias. Esto valida la separación de configuración, no la configuración efectiva de cPanel.
- Pruebas nuevas: frescura nunca sincronizada, actualizada, vencida, fallida, parcial, independencia facturas/pagos y advertencias de panel.
- Suite general: `Ran 155 tests`; 153 pasan y permanecen 2 errores `PermissionError [WinError 5]` preexistentes, sin fallos de P1-B. Se reproducen en `tienda/tests.py:70` (`MediaStorageTests.test_safe_media_url_returns_empty_for_missing_local_file`) y `tienda/tests.py:765-766` (`PortalClienteTests.test_panel_producto_editar_uploads_image_without_invalid_storage_error`), ambos al crear/eliminar directorios temporales o guardar media bajo la ruta temporal aislada de Windows.

### Veredicto actualizado

Los hallazgos F-006 y F-007 quedan corregidos en código y pruebas locales, con la salvedad de que la configuración efectiva de producción continúa pendiente de verificación en cPanel. El veredicto global permanece condicionado por los riesgos de auditoría no incluidos en P1-B.

## 18. Actualización P2 — privacidad, rendimiento y alertas

**Fecha de actualización:** 2026-10-08

### F-008 — Minimización de payloads externos

Los campos normalizados continúan siendo la fuente de visualización y conciliación. Las futuras sincronizaciones de facturas y pagos ya no guardan la respuesta completa: conservan identificador, numeración, fechas, cliente mínimo para conciliación, importes normalizados, moneda, estado, saldo, referencias limitadas e ítems limitados. Los clientes e ítems ya utilizaban estructuras técnicas depuradas; no se amplió su almacenamiento con el payload recibido.

No se guardan credenciales, encabezados HTTP, tokens ni respuestas completas. No se ejecutaron purgas históricas. Los `original_data` existentes no fueron modificados y requieren una revisión/retención aprobada antes de cualquier limpieza.

### F-009 — Conciliación y cartera

Los conciliadores cargan índices de nombres o identificaciones una vez por lote, en lugar de recorrer el catálogo completo por cada registro. La cartera evita recalcular filas cuando el dashboard ya las construyó y conserva `select_related` para clientes.

La medición reproducible `tienda.test_p2_estabilizacion.P2BatchReconciliationTests` verifica que cada índice de catálogo se construye con un máximo de tres consultas observables en SQLite local, con conjuntos de prueba representativos. La prueba específica P2 y las regresiones de cartera/centro de control pasan; no se realizó una prueba de carga destructiva ni se incorporó infraestructura externa.

### F-010 — Alertas idempotentes

`Notificacion` incorpora identidad estable por evento (`event_key`), estado abierta/resuelta y `resuelta_at`. Las migraciones `0027_notificacion_estado_notificacion_event_key_and_more` y `0028_remove_notificacion_unique_notification_event_per_user_and_more` son no destructivas. Los eventos de control usan una restricción única por usuario/evento y una inserción protegida contra carreras; las condiciones ausentes se resuelven y una condición reaparecida reactiva la misma notificación.

Las alertas financieras no se generan a partir de staging incompleto. No se configuró cron, scheduler ni envío automático.

### Validación P2

- `python manage.py check`: correcto.
- `python manage.py makemigrations --check --dry-run`: sin cambios pendientes después de aplicar las migraciones locales.
- Pruebas P2, importación, conciliación, cartera y centro de control: 18 pruebas correctas en el último bloque combinado.
- Suite general: `Ran 160 tests`; 158 pasan y permanecen únicamente los 2 `PermissionError [WinError 5]` preexistentes de media/temporales documentados en la sección P1-B. No quedaron fallos funcionales P2.

### Riesgos residuales P2

- Los payloads históricos completos siguen presentes hasta aprobar una política de retención y ejecutar una limpieza controlada.
- La medición de rendimiento es de consultas y lotes locales pequeños/representativos; aún falta validar volumen real en MySQL/MariaDB.
- SQLite serializa escrituras concurrentes; la unicidad protege eventos duplicados, pero la operación debe validarse bajo el motor real de producción.

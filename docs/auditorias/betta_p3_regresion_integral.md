# BettaApp — P3 regresión integral

**Fecha:** 2026-10-08  
**Alcance:** estabilización local, regresión comercial, financiera, integración y seguridad.  
**Restricciones:** sin producción, sin cPanel, sin escrituras hacia Alegra y sin datos reales de Alegra.

## 1. Resumen ejecutivo

La regresión integral local terminó correctamente: **160 pruebas ejecutadas, 160 correctas, 0 fallos, 0 errores y 0 omitidas**, en aproximadamente **165,7 segundos**.

Los dos `PermissionError [WinError 5]` recurrentes quedaron resueltos en las pruebas sin modificar la gestión real de archivos multimedia. La causa fue el directorio temporal administrado por el sandbox de Windows, ubicado bajo `AppData\Local\Packages\sandbox...\Temp`, cuyo ACL impedía crear o limpiar subdirectorios durante los tests.

No se identificaron nuevos hallazgos críticos en los flujos cubiertos. El sistema queda apto para continuar con validación controlada, pero no se certifica producción ni compatibilidad efectiva con el hosting hasta ejecutar verificaciones en un entorno equivalente.

## 2. PermissionError — causa y solución

### Evidencia

Los errores estaban en:

- `tienda/tests.py:70`: `MediaStorageTests.test_safe_media_url_returns_empty_for_missing_local_file`.
- `tienda/tests.py:765-766`: `PortalClienteTests.test_panel_producto_editar_uploads_image_without_invalid_storage_error`.

El traceback ocurría al crear la carpeta `productos` o durante la limpieza de `TemporaryDirectory`, con `PermissionError [WinError 5]`. No se observó un archivo multimedia abierto que explicara el bloqueo.

### Verificación de la causa

Los mismos dos tests pasaron al ejecutar con `TEMP/TMP` apuntando al workspace. Esto aisló la causa en los permisos del root temporal de Windows, no en `FileSystemStorage`, `MEDIA_ROOT`, `Producto.save()` ni en la gestión real de media.

### Corrección aplicada

Se añadió en `tienda/tests.py` un helper de uso exclusivo para pruebas:

```python
def local_test_temporary_directory():
    return tempfile.TemporaryDirectory(dir=str(settings.BASE_DIR))
```

Los dos tests utilizan ahora ese directorio local temporal. La corrección:

- no cambia `MEDIA_ROOT` de la aplicación;
- no cambia `STORAGES` ni `FileSystemStorage`;
- no ignora excepciones de limpieza;
- no desactiva pruebas;
- mantiene la limpieza normal de `TemporaryDirectory`.

## 3. Pruebas ejecutadas

| Comando | Resultado |
|---|---|
| `python manage.py check` | Correcto, sin problemas |
| `python manage.py makemigrations --check --dry-run` | `No changes detected` |
| `python manage.py test tienda` | 160/160 correctas, 0 fallos, 0 errores, 0 omitidas |

Tiempo de suite: **165,718 segundos**.

También se validaron de forma aislada los dos tests que fallaban anteriormente: **2/2 correctos**.

## 4. Regresión comercial

La suite existente y las pruebas específicas cubren el flujo:

```text
Cliente → Punto de venta → Proyecto → Solicitud → Cotización → Venta → Producción
```

Se verificaron:

- pertenencia de puntos de venta, proyectos, solicitudes, cotizaciones y ventas al mismo cliente;
- proyectos con varias sedes y proyectos sin sedes;
- solicitudes y cotizaciones con punto de venta opcional;
- conversión de cotización a venta e idempotencia;
- snapshots históricos de `VentaItem`;
- estados comerciales y relaciones con producción;
- permisos, CSRF y prevención de accesos cruzados.

No se modificaron reglas comerciales ni el motor de precios.

## 5. Regresión financiera

Se validaron:

- facturas activas, abiertas y cerradas;
- facturas anuladas, borradores y estados desconocidos;
- saldos ausentes;
- pagos parciales y facturas pagadas;
- cartera vencida y no vencida;
- rangos de antigüedad;
- indicadores de cartera y facturación;
- exclusión de documentos no verificables;
- independencia de la frescura de facturas y pagos;
- advertencias en cartera, informes y centro de control.

No se usaron respuestas reales de Alegra. Las pruebas emplean datos locales, mocks y fixtures controlados.

## 6. Regresión de integraciones

Se comprobaron con mocks:

- paginación de productos, clientes, facturas y pagos;
- idempotencia de staging;
- mapeos externos y prevención de duplicados;
- conciliación por identificadores y conflictos;
- sincronizaciones parciales y errores de API;
- minimización de payloads futuros;
- ausencia de métodos POST, PUT, PATCH o DELETE en el cliente de Alegra;
- preservación de registros anteriores ante sincronizaciones parciales.

No se realizaron llamadas externas durante la regresión.

## 7. Seguridad

La regresión cubrió autenticación, permisos administrativos, CSRF, validaciones de pertenencia, protección contra IDOR y vínculos financieros entre clientes.

También se verificó que los nuevos payloads depurados no conserven headers ni credenciales y que las alertas tengan identidad estable y restricción única.

La configuración de producción real no fue validada porque cPanel está fuera del alcance. Deben verificarse allí `DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS`, HTTPS, cookies seguras, CSRF, HSTS, proxy y permisos de media.

## 8. Migraciones 0026, 0027 y 0028

- `0026_p1a_integridad_referencial.py`: restricción de mapeos externos activos. Aplicada localmente.
- `0027_notificacion_estado_notificacion_event_key_and_more.py`: estado, identidad de evento y fecha de resolución de notificaciones.
- `0028_remove_notificacion_unique_notification_event_key_and_more.py`: ajuste de `event_key` nullable y unicidad portable por usuario/evento.

Las tres migraciones están aplicadas en SQLite local. `makemigrations --check --dry-run` no detecta cambios pendientes.

SQLite validó las restricciones e índices. La compatibilidad efectiva de la restricción de `ExternalObjectMap` de P1-A y del esquema completo debe probarse contra una instancia MySQL/MariaDB equivalente antes del despliegue.

## 9. Hallazgos nuevos

No se confirmaron hallazgos nuevos críticos, altos o medios durante esta regresión.

La corrección de `PermissionError` fue específica de los tests y no representa un cambio de infraestructura ni una modificación del manejo real de archivos.

## 10. Riesgos residuales

- No se ha validado la integración con respuestas reales de la cuenta Alegra.
- No se ha comprobado el comportamiento con volúmenes altos en MySQL/MariaDB.
- No se ha verificado la configuración efectiva de cPanel, proxy HTTPS, media y backups.
- Los payloads históricos completos de Alegra siguen sujetos a la política de retención pendiente; no se ejecutaron purgas.
- Los indicadores financieros dependen de la cobertura y frescura de las sincronizaciones disponibles.

## 11. Validaciones pendientes con datos reales

Antes de producción se requiere validar, sin habilitar escrituras:

1. Respuestas reales de `/items`, `/contacts`, `/invoices` y `/payments`.
2. Estados fiscales, saldos, monedas y fechas de vencimiento de Alegra Colombia.
3. Paginación y límites reales de la cuenta.
4. Tiempo de respuesta y volumen esperado.
5. Migraciones sobre una base MySQL/MariaDB de prueba.
6. Backup, restore, permisos de media y configuración HTTPS de cPanel.

## 12. Veredicto técnico

**APTO PARA CONTINUAR EN DESARROLLO LOCAL CONTROLADO.**

La suite local está completamente verde y no se observaron regresiones técnicas dentro del alcance P3. El resultado no constituye certificación de producción ni autorización para escribir en Alegra o desplegar.

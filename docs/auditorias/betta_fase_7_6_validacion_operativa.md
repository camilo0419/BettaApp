# BettaApp — Fase 7.6: validación operativa de sincronización de clientes

Fecha de ejecución: 2026-10-08  
Entorno: repositorio local, Django con SQLite local; no se accedió a producción ni a cPanel.

## Veredicto

**VALIDACIÓN OPERATIVA SATISFACTORIA EN MODO SEGURO.**

El flujo de lectura, planificación, actualización local simulada, idempotencia y protección contra escrituras externas quedó validado. La sincronización bidireccional todavía no es operativa hacia Alegra: el adaptador externo permanece simulado y no contiene una vía habilitada para POST, PUT, PATCH o DELETE.

## Ejecución real GET-only

Se ejecutó:

```text
python manage.py alegra_sync_clientes --dry-run --simulate-outbound --limit 1000 --max-pages 20 --pause 0 --max-retries 2 --timeout 15 --output docs/auditorias/betta_sync_clientes_ultima_ejecucion.md --no-color
```

Resultados agregados:

| Métrica | Resultado |
|---|---:|
| Contactos consultados | 167 |
| Páginas | 6 |
| Cobertura | Completa; fin de paginación informado por Alegra |
| Errores HTTP/API | 0 |
| Contactos ya vinculados | 156 |
| Duplicados para revisión | 8 |
| Inválidos | 3 |
| Actualizaciones evaluadas | 156, todas `SYNCED` |
| Clientes locales pendientes de salida | 2 |
| Cambios locales aplicados | 0 |
| Escrituras hacia Alegra | 0 |

El reporte de ejecución se conserva en [betta_sync_clientes_ultima_ejecucion.md](betta_sync_clientes_ultima_ejecucion.md). No contiene payloads completos ni datos personales.

## Clientes locales pendientes

Se compararon los 2 clientes locales sin mapeo contra los 167 contactos recuperados, únicamente en memoria y sin mostrar valores identificables:

| Comparación | Coincidencias |
|---|---:|
| Identificación normalizada | 0 |
| Correo normalizado | 0 |
| Nombre normalizado | 0 |

No se creó ningún vínculo automático. La ausencia de coincidencia en estos tres atributos no autoriza una creación externa ni sustituye una revisión funcional posterior.

## Comportamiento validado

### Entrada desde Alegra

El servicio reutiliza el cliente de solo lectura, consulta contactos tipo cliente con `mode=advanced`, pagina con límite explícito y deduplica por ID externo. Los IDs ya mapeados producen `NO_ACTION`; las identificaciones repetidas permanecen en revisión y los datos insuficientes se omiten.

### Actualizaciones

Se validó con mocks que:

- un contacto nuevo puede crear un cliente local y un único `ExternalObjectMap` dentro de una transacción;
- repetir el mismo contacto no crea otro cliente ni otro mapeo;
- los cambios remotos no conflictivos se aplican localmente de forma explícita;
- un valor remoto vacío no borra un valor local existente;
- cambios simultáneos se clasifican como `CONFLICT` y no se aplican;
- un correo repetido entre clientes no se trata como conflicto de identidad.

### Salida desde BettaApp

El adaptador `SafeAlegraWriteAdapter` genera planes allowlisted e idempotentes, pero devuelve operaciones simuladas. No se habilitó transporte de escritura real. Los 2 clientes locales pendientes solo se reportan como candidatos; no se preparó ni envió una operación externa real.

### Idempotencia, concurrencia y auditoría

El comando usa un lock de ejecución con creación exclusiva de archivo temporal, exige `--apply-local` y confirmación exacta para cualquier cambio local, y bloquea aplicación si la cobertura es incompleta, hay errores o existen conflictos. La aplicación local usa transacciones y restricciones de mapeo. En esta ejecución dry-run no se creó `SyncAuditLog` de escritura ni se modificó staging.

## Migraciones y datos locales

`python manage.py makemigrations --check --dry-run` informó **No changes detected**. No se generaron ni aplicaron migraciones en esta fase. No fue necesario crear respaldo porque no se modificó la SQLite comercial; las pruebas usaron la base temporal de Django.

Conteos observados antes y después del dry-run: 158 clientes locales y 156 mapeos activos de contactos. No se alteraron puntos de venta, relaciones comerciales, cartera, ventas ni cotizaciones.

## Pruebas

Comandos ejecutados:

```text
python manage.py check --no-color
python manage.py makemigrations --check --dry-run --no-color
python manage.py test tienda.test_fase7_6 tienda.test_fase7_4 tienda.test_alegra_client_import tienda.test_alegra_preimport_clients tienda.test_alegra_bidirectional_clients tienda.test_alegra_contacts tienda.test_alegra_client --no-color
```

Resultado final: **50 pruebas correctas, 0 fallos, 0 errores**.

La primera ejecución de la prueba nueva detectó correctamente un conflicto en un fixture que representaba una modificación local simultánea; se corrigió el fixture para modelar el caso no conflictivo y la regresión final terminó completamente en verde.

## Estado por capacidad

| Capacidad | Estado |
|---|---|
| GET de contactos Alegra | Validado contra Alegra real |
| Paginación y cobertura | Validado contra Alegra real |
| Conciliación por ID externo | Implementado y probado |
| Importación local transaccional | Probada con mocks/base temporal; no ejecutada en esta fase |
| Actualización local controlada | Probada con mocks |
| Creación desde BettaApp | Plan/adaptador simulado |
| Creación o actualización en Alegra | No habilitada; sin POST/PUT/PATCH/DELETE |
| Lock e idempotencia | Probados |
| Panel administrativo | Revisado; muestra modo seguro e indicadores existentes |

## Riesgos pendientes

1. Para habilitar escrituras reales se requiere autorización independiente, un transporte de escritura separado del cliente GET-only, política de reintentos y conciliación para respuestas perdidas.
2. Los 8 duplicados y 3 inválidos requieren revisión administrativa; no deben resolverse por nombre o correo.
3. Los 2 clientes locales pendientes no tienen evidencia de correspondencia en el catálogo actual y no deben vincularse automáticamente.
4. La validación real cubre el catálogo observado en esta ejecución, no constituye autorización para sincronización automática continua.

## Archivos de esta fase

- `tienda/services/alegra_bidirectional_clients.py`: normalización de identificación para comparación segura.
- `tienda/test_fase7_6.py`: pruebas operativas de entrada, actualizaciones, conflictos, repetición y correos compartidos.
- `docs/auditorias/betta_fase_7_6_validacion_operativa.md`: este informe.
- `docs/auditorias/betta_sync_clientes_ultima_ejecucion.md`: resultado agregado del dry-run real GET-only.

No se ejecutó importación local, no se modificó la base comercial y no se realizaron escrituras hacia Alegra.

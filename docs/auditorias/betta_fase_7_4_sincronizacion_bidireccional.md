# BettaApp — Fase 7.4: identidad y sincronización bidireccional

## 1. Resumen ejecutivo

Se preparó la evolución de la identidad de clientes para que el correo sea un atributo repetible y no una identidad de cliente. La identidad local continúa siendo `Cliente.id`; la identidad externa continúa siendo `Alegra contacts.id`; la relación estable sigue estando en `ExternalObjectMap`.

La migración `0029_remove_cliente_cliente_email_unico_si_existe.py` fue generada, pero **no fue aplicada** a la SQLite comercial. Los dos contactos rechazados en la importación inicial no fueron recuperados ni importados en esta fase.

El motor bidireccional sigue en modo seguro: las operaciones externas son GET o adaptadores simulados. No se añadieron métodos POST, PUT, PATCH ni DELETE.

## 2. Restricción de correo identificada

`Cliente.Meta` tenía la restricción parcial `cliente_email_unico_si_existe`, que impedía representar dos clientes distintos con el mismo correo. Esto causó los dos rechazos de Fase 7.3.

Se eliminó únicamente esa restricción del estado del modelo. Se conservaron:

- validación de formato de correo;
- unicidad del usuario de autenticación en `User`;
- unicidad del documento almacenado en `Cliente`;
- todas las restricciones de `ExternalObjectMap`;
- relaciones comerciales y de puntos de venta.

La validación de registro de portal ya no rechaza un cliente solo porque otro cliente comercial tenga el mismo correo; sí mantiene la verificación de colisión con cuentas `User`, necesaria para autenticación.

## 3. Migración

Generada:

`tienda/migrations/0029_remove_cliente_cliente_email_unico_si_existe.py`

No aplicada sobre `db.sqlite3`. La base comercial conserva su esquema actual hasta una autorización posterior con respaldo y verificación. `makemigrations --check --dry-run` queda limpio porque la migración pendiente representa el cambio declarado en los modelos.

## 4. Recuperación dirigida de los dos contactos

Se agregó `alegra_recuperar_clientes`, que solo consulta por ID mediante GET, procesa en memoria y no modifica clientes, staging ni mapeos.

Uso futuro, después de identificar localmente los dos IDs externos y autorizar una fase separada:

```text
python manage.py alegra_recuperar_clientes --external-id ID_1 ID_2
```

El comando deduplica IDs, rechaza respuestas incompletas y genera un informe sin nombres, documentos, correos ni teléfonos. No existe una opción de importación efectiva en este comando.

En esta fase:

- los dos contactos no fueron consultados de forma dirigida;
- no se modificó la base comercial;
- no se fusionó ni vinculó ningún cliente;
- la recuperación queda pendiente de identificar los IDs externos y revisar su correo/documento sin ambigüedad.

## 5. Motor bidireccional seguro

Se reutilizaron `AlegraReadOnlyClient`, `ExternalObjectMap`, `SyncAuditLog`, `BidirectionalClientSync` y el planificador de preimportación.

Capacidades implementadas:

- detección de clientes locales sin mapeo mediante `pending_local_clients`;
- generación de payload allowlistado para una futura creación en Alegra;
- planes `PENDING` en memoria, sin POST real;
- claves de idempotencia estables;
- respuesta perdida convertida en `NEEDS_RECONCILIATION`;
- reintento simulado con la misma clave, sin repetir una escritura externa;
- detección de conflictos por campos compartidos;
- registro declarativo de capacidades por recurso;
- prevención de bucles mediante separación entre plan externo y confirmación de mapeo.

El motor no marca operaciones reales como `SYNCED`, no inventa IDs externos y no altera automáticamente campos locales.

## 6. Matriz de integración

| Recurso | Sistema de origen | Identidad interna | Identidad externa | Dirección | Operaciones habilitadas | Conflicto | Estado |
|---|---|---|---|---|---|---|---|
| Clientes | Betta después de importación inicial | `Cliente.id` | `contacts.id` | Ambos sentidos diseñados | GET, creación/actualización simulada | Revisión manual | Seguro/simulado |
| Productos | Betta | `Producto.id` | `items.id` | Alegra → Betta para revisión | GET | Revisión manual | Solo lectura |
| Cotizaciones | Betta | `Cotizacion.id` | `estimates.id` futuro | Ninguna escritura | GET futuro | Revisión manual | No habilitado |
| Ventas | Betta | `Venta.id` | Documento externo futuro | Ninguna escritura | Local | Revisión comercial | No habilitado |
| Facturas | Alegra | `AlegraInvoiceStaging.id` | `invoices.id` | Alegra → Betta | GET | Fuente financiera externa | Solo lectura |
| Pagos | Alegra | `AlegraPaymentStaging.id` | `payments.id` | Alegra → Betta | GET | Fuente financiera externa | Solo lectura |
| Cartera | Alegra | cálculo interno | Facturas/pagos Alegra | Alegra → Betta | GET y cálculo | Fuente financiera externa | Solo lectura |

La matriz está representada declarativamente por `INTEGRATION_CAPABILITIES`; no activa escrituras para recursos distintos de clientes ni cambia la lógica financiera.

## 7. Integridad e idempotencia

- El ID externo de contacto es la clave principal de conciliación.
- La identificación repetida no fusiona contactos.
- El correo repetido no crea una colisión de identidad.
- `ExternalObjectMap` conserva unicidad por sistema, recurso e ID externo.
- Un cliente local no recibe automáticamente dos contactos externos activos.
- Los planes de salida usan la misma clave idempotente al reintentar.
- Una respuesta perdida queda pendiente de conciliación; no se repite ciegamente una creación.
- La indisponibilidad de Alegra no impide crear un cliente local; el plan queda `PENDING`.

## 8. Pruebas

Ejecutado:

```text
python manage.py check
System check identified no issues (0 silenced).

python manage.py makemigrations --check --dry-run
No changes detected

python manage.py test tienda.test_fase7_4 tienda.test_alegra_client_import tienda.test_alegra_preimport_clients tienda.test_alegra_bidirectional_clients tienda.test_alegra_contacts tienda.test_alegra_client
Ran 44 tests in 3.265s
OK
```

Las pruebas cubren correo repetido, cliente sin correo, mapeos, recuperación dirigida, planes locales, reintentos, respuesta perdida, capacidades y ausencia de escrituras externas. La migración fue usada por la base temporal de tests; no fue aplicada a la base SQLite comercial.

## 9. Estado de recuperación

Los dos contactos con correo duplicado de Fase 7.3 no fueron recuperados. El cambio de modelo elimina el bloqueo estructural para una ejecución posterior, pero no autoriza por sí solo crear registros en la base comercial. Debe ejecutarse primero la migración pendiente y luego una recuperación dirigida revisada.

## 10. Riesgos pendientes

1. La migración 0029 requiere respaldo y aplicación explícita en local; no se ejecutó.
2. La unicidad fiscal sigue siendo una restricción local; deben revisarse formatos y duplicados antes de vincular.
3. La cola persistente de operaciones `PENDING` todavía no existe; por ahora los planes se mantienen en memoria.
4. El adaptador de escrituras Alegra continúa deshabilitado.
5. Los timestamps comparables entre Betta y Alegra deben validarse antes de resolver conflictos automáticamente.
6. Los ocho contactos con identificación repetida y los tres inválidos requieren revisión manual.

## 11. Qué falta para sincronización real

- Revisar y aplicar la migración 0029 con respaldo local.
- Recuperar dirigidamente los dos IDs externos pendientes.
- Definir una cola persistente y auditoría de operaciones pendientes.
- Probar escrituras únicamente en sandbox autorizado de Alegra.
- Implementar confirmación de respuesta y conciliación de respuestas perdidas.
- Autorizar por separado la primera escritura real; hasta entonces todos los adaptadores permanecen simulados.

## Veredicto

**Arquitectura preparada, sincronización bidireccional no operativa.** La identidad por ID y el motor seguro están implementados y probados, pero no existen escrituras externas reales ni recuperación comercial ejecutada en esta fase.

# BettaApp — Fase 7.7: adaptador real de escritura hacia Alegra

Fecha: 2026-10-08  
Entorno: desarrollo local. No se accedió a producción ni a cPanel y no se enviaron solicitudes de escritura a Alegra.

## Resultado ejecutivo

Se implementó un adaptador HTTP real para contactos, separado del cliente GET-only existente, junto con un registro durable de operaciones y una capa transaccional para asociar el ID externo mediante `ExternalObjectMap`.

El transporte de escritura permanece **bloqueado por defecto**. No se habilitó ningún botón, comando ni flujo de negocio que ejecute POST o PUT contra la cuenta de Alegra. La funcionalidad está preparada para una prueba individual futura, pero no se declara operativa hasta contar con autorización explícita, configuración de entorno controlada y una validación real independiente.

## Contrato oficial verificado

La documentación oficial consultada confirma:

| Operación | Endpoint | Método | Contrato observado |
|---|---|---|---|
| Crear contacto | `/contacts` | POST | `name` requerido; payload JSON de contacto; respuesta exitosa con ID esperado |
| Editar contacto | `/contacts/{id}` | PUT | actualización por ID; respuestas documentadas 201/400/404 |
| Consultar detalle | `/contacts/{id}` | GET | detalle por ID |
| Buscar candidatos | `/contacts` | GET | filtros `identification`, `name`, `type`, `mode`, `limit` y `metadata` |

La autenticación documentada es Basic Auth. La lista de contactos admite `type=client`, `mode=advanced` y máximo 30 registros por solicitud. La documentación de contactos no confirma una clave idempotente HTTP para POST; por eso el adaptador usa una clave interna y bloquea reintentos automáticos ante resultado incierto.

Fuentes oficiales:

- [Crear un contacto](https://developer.alegra.com/reference/post_contacts)
- [Editar contacto](https://developer.alegra.com/reference/editcontact)
- [Obtener detalle de un contacto](https://developer.alegra.com/reference/contactsdetails-1)
- [Listado de contactos y filtros](https://developer.alegra.com/reference/listcontacts-1)
- [Información general de la API](https://developer.alegra.com/docs/informaci%C3%B3n-general)

La API general menciona REST con GET, POST, PUT y PATCH, pero el recurso de contactos verificado documenta creación con POST y edición con PUT. El adaptador no implementa PATCH ni DELETE para contactos.

## Arquitectura implementada

### Transporte

`tienda/services/alegra_write.py` contiene `AlegraWriteClient` con:

- `create_contact()` → POST `/contacts`.
- `update_contact()` → PUT `/contacts/{id}`.
- `get_contact()` → reutiliza el cliente GET-only para la lectura de confirmación.
- `find_candidates()` → GET con filtros documentados.
- timeout configurable y Basic Auth exclusivamente desde variables de entorno;
- errores HTTP normalizados sin conservar cuerpos sensibles;
- ningún reintento automático de escrituras.

### Capa de negocio y persistencia

`AlegraContactWriteService` separa:

1. preparación y allowlist del payload;
2. creación de una operación pendiente;
3. ejecución autorizada del transporte;
4. creación atómica del `ExternalObjectMap`;
5. confirmación de actualización mediante GET posterior.

El payload solo usa campos compatibles de contacto: nombre, identificación, tipo cliente, estado, correo, teléfonos y dirección. No envía proyectos, puntos de venta, precios, cartera, producción ni otros campos exclusivos de BettaApp. No se incluyen valores vacíos como sobrescrituras.

## Identidad e idempotencia

- La identidad local es `Cliente.id`.
- La identidad externa es `contacts.id` de Alegra.
- La correspondencia se crea únicamente en `ExternalObjectMap`.
- Nombre, correo y NIT no se usan como identidad definitiva.
- Una operación de creación usa una clave interna derivada de cliente y payload.
- Una operación sincronizada no se repite.
- Una operación en `needs_reconciliation` bloquea cualquier nuevo POST automático.
- Si el POST termina con timeout, error de transporte o respuesta sin ID, el estado es `NEEDS_RECONCILIATION`.
- Si el ID retornado ya pertenece a otro objeto local, se bloquea la asociación y se conserva el conflicto.

## Interruptor de seguridad

El transporte exige simultáneamente:

1. `ALEGRA_EXTERNAL_WRITES_ENABLED=true` en el entorno de ejecución.
2. una autorización explícita creada en memoria para un cliente concreto;
3. operación exacta (`POST` o `PUT`);
4. entorno explícito `production`;
5. confirmación operativa positiva.

La ausencia de la variable equivale a denegación. La variable por sí sola no autoriza nada. El comando `alegra_sync_clientes`, sus dry-runs, los importadores y las pruebas no reciben esta autorización ni pueden ejecutar escrituras externas.

No se modificó el archivo `.env`.

## Estados persistidos

Se creó `AlegraWriteOperation` para conservar solo metadatos operativos:

- cliente local y sistema externo;
- operación create/update;
- ID externo cuando exista;
- estado;
- clave idempotente;
- intentos y última fecha de intento;
- código y mensaje de error depurado;
- razón de conciliación;
- metadatos técnicos mínimos.

No se guarda el payload completo, el token, el encabezado Authorization ni respuestas completas de Alegra.

La migración `0030_alegrawriteoperation.py` fue generada y está pendiente de aplicación. No se aplicó a la SQLite comercial local.

## Manejo de errores

- 400: operación fallida; requiere corrección de datos.
- 401/403: operación fallida por autenticación o autorización.
- 404: operación fallida para actualización o detalle inexistente.
- 409: se conserva como conflicto cuando Alegra lo devuelva o exista contradicción de identidad.
- 429 y 5xx: no se reintentan automáticamente; la operación queda fallida y requiere política operativa posterior.
- timeout/error de transporte: `NEEDS_RECONCILIATION`, sin repetir POST.
- respuesta exitosa sin ID: `NEEDS_RECONCILIATION`.
- creación concurrente del mapeo: la restricción de `ExternalObjectMap` protege la identidad y deja la operación para revisión si hay contradicción.

## Pruebas

Comandos ejecutados:

```text
python manage.py check --no-color
python manage.py makemigrations --check --dry-run --no-color
python manage.py test tienda.test_fase7_7 tienda.test_fase7_6 tienda.test_fase7_4 tienda.test_alegra_client tienda.test_alegra_bidirectional_clients --no-color
```

Resultado: **32 pruebas correctas, 0 fallos y 0 errores** en la suite focalizada de esta fase y regresiones relacionadas.

Se cubrió:

- allowlist y omisión de campos vacíos;
- POST bloqueado sin autorización y sin llamada de red;
- preparación idempotente;
- cliente ya vinculado;
- creación exitosa y mapeo atómico;
- timeout/respuesta incierta y bloqueo de repetición;
- respuesta sin ID;
- actualización con alcance PUT y confirmación GET;
- conflictos y ausencia de cambios autorizables;
- ausencia de POST/PUT/PATCH/DELETE reales desde pruebas.

## Estado de preparación

| Capacidad | Estado |
|---|---|
| Contrato de contactos | Verificado en documentación oficial |
| GET de detalle y candidatos | Implementado mediante cliente existente |
| POST de creación | Implementado, bloqueado por defecto, probado con fake transport |
| PUT de actualización | Implementado, bloqueado por defecto, probado con fake transport |
| Mapeo atómico | Implementado y probado en base temporal |
| Estados durables | Implementados en modelo nuevo |
| Migración | Generada, no aplicada |
| Escritura real hacia Alegra | No ejecutada |
| Sincronización saliente general | No habilitada |

## Procedimiento para una primera prueba real futura

1. Respaldar la SQLite local y verificar el entorno de ejecución.
2. Aplicar la migración 0030 únicamente después de revisar el respaldo y autorizarlo.
3. Seleccionar un único cliente de prueba previamente conciliado, sin mapeo activo.
4. Verificar por GET que no exista un contacto externo inequívocamente coincidente que deba revisarse manualmente.
5. Configurar el entorno de escritura en un proceso aislado, sin modificar `.env` de forma permanente.
6. Crear una autorización acotada al cliente y a `POST`.
7. Ejecutar una sola operación, registrar solo metadatos y confirmar el ID por GET.
8. Revisar `ExternalObjectMap` y `AlegraWriteOperation` antes de cualquier segunda operación.
9. Detenerse ante timeout, respuesta sin ID, contradicción o error de permisos.

Este procedimiento requiere autorización posterior. No fue ejecutado en Fase 7.7.

# Alegra — Fase 2: clientes y puntos de venta

## Alcance y resultado

BettaApp conserva la propiedad de los clientes, contactos operativos y puntos de venta. Alegra se consulta mediante `GET /contacts` con `mode=advanced`, paginación de hasta 30 registros por solicitud y staging local. No se ejecutan escrituras en Alegra ni se crean puntos de venta automáticamente.

La cuenta del diagnóstico existente mostró contactos con estos campos: `id`, `uuid`, `name`, `identification`, `phonePrimary`, `phoneSecondary`, `mobile`, `email`, `status`, `type`, `address.zipCode`, `address.department`, `address.country`, `address.address`, `address.city`, `term`, `seller`, `priceList`, `accounting` e `internalContacts`. Los objetos completos no se guardan en auditoría; el staging conserva solo campos operativos y una lista técnica allow-listed, incluyendo el conteo de contactos internos.

## Mapeo Alegra ↔ Betta

| Alegra | Betta | Regla |
| --- | --- | --- |
| `id`, `uuid` | `ExternalObjectMap` / `AlegraContactStaging` | Identidad externa estable; no usar nombre como clave. |
| `name` | `Cliente.nombre` / `razon_social` al importar empresa | Se importa como dato inicial revisable. |
| `identification` | `Cliente.identificacion` | Se separa el DV cuando viene como `NIT-DV`; la identificación se normaliza solo para comparar. |
| `type` | `Cliente.tipo_cliente` | `company`/`empresa` se interpreta como empresa; requiere revisión si el valor cambia. |
| `phonePrimary` | `Cliente.telefono` | Dato inicial, editable en Betta. |
| `phoneSecondary` | `Cliente.telefono_secundario` | No altera reglas comerciales. |
| `mobile` | `Cliente.celular` | `whatsapp` existente permanece compatible. |
| `email` | `Cliente.email` | No se usa por sí solo para vincular. |
| `address.*` | Dirección, ciudad, departamento, país y código postal de `Cliente` | Datos de contacto, no punto de venta. |
| `internalContacts` | No se convierte automáticamente en `ClienteContacto` ni `ClientePuntoVenta` | Solo se registra su presencia de forma técnica. |
| `term`, `priceList`, `accounting`, `seller` | `technical_data` | Referencia externa; no se transforman en cartera, crédito o reglas comerciales. |

Los campos genéricos de Alegra son variables según país y configuración. Los campos observados de Colombia son identificación, DV y departamento; la respuesta real puede omitirlos o cambiar su forma. El código tolera ausencia de campos y no registra tokens, headers ni payloads personales completos.

## Modelos y migración

- `Cliente`: agrega teléfono secundario, celular, DV, departamento, país y código postal sin eliminar ni renombrar campos anteriores.
- `ClientePuntoVenta`: agrega código interno, departamento y `es_principal`, manteniendo la relación existente `Cliente 1:N ClientePuntoVenta`.
- `AlegraContactStaging`: snapshot depurado, clasificación, candidato local, decisión bloqueada y datos técnicos mínimos.
- `ExternalObjectMap`: conserva la identidad Alegra de tipo `contacts` y apunta al `Cliente` local.
- `SyncAuditLog`: registra consulta, clasificación, importación, vinculación e ignorado.

La migración es `tienda/migrations/0021_alegracontactstaging_cliente_celular_and_more.py`, aplicada únicamente en SQLite local. No se modificaron modelos de productos, precios, cotizaciones, solicitudes ni producción.

## Reglas de conciliación

1. Un mapeo externo activo válido clasifica como vinculado.
2. Sin mapeo, la identificación normalizada es la única señal automática de candidato.
3. Nombre y correo no confirman equivalencias; no se usan para vincular automáticamente.
4. Un único candidato por identificación queda como coincidencia probable y requiere vinculación manual.
5. Múltiples candidatos quedan como conflicto.
6. Las decisiones manuales quedan bloqueadas frente a futuras consultas.
7. Alegra no sobrescribe clientes Betta.

## Puntos de venta

`ClientePuntoVenta` ya era el modelo correcto. Un cliente puede existir sin puntos y tener varios; cada punto pertenece a un único cliente mediante FK. El formulario administrativo permite dirección, ciudad, departamento, contacto, teléfono, correo, código interno, estado y un principal opcional. La base impide más de un punto principal activo por cliente.

Las direcciones de Alegra y `internalContacts` no crean puntos de venta. La creación y edición se mantiene en la ficha del cliente, con POST, CSRF y permisos de panel.

## Operaciones disponibles

Panel: `/panel/integraciones/alegra/clientes/`.

- Consulta paginada de contactos.
- Búsqueda y filtro por clasificación.
- Clasificación masiva.
- Vista previa para importar o ignorar.
- Importación individual/masiva de clientes nuevos, sin puntos automáticos.
- Vinculación manual protegida contra reasignar un ID externo a otro cliente.
- Ignorado y auditoría.

Las operaciones externas siguen siendo exclusivamente GET. Las operaciones locales usan POST, CSRF, permisos `view_alegracontactstaging`/`change_alegracontactstaging`, transacciones e idempotencia.

## Relaciones futuras

`Proyecto`, `Solicitud` y `Cotizacion` ya se relacionan opcionalmente con `Cliente`; esta fase no agrega aún FK a punto de venta para no alterar formularios históricos. En una fase posterior se podrá agregar una FK nullable con validación de pertenencia al cliente y migración compatible.

## Pruebas

- `python manage.py check`: correcto.
- `python manage.py makemigrations --check --dry-run`: sin cambios.
- Pruebas específicas de clientes, Fase 1 y cliente Alegra: 24/24 correctas.
- Suite general: 103/105 correctas; las dos fallas son pruebas existentes de almacenamiento multimedia que no pueden crear/eliminar directorios temporales por permisos del sandbox.

## Decisiones pendientes y limitaciones

- Confirmar con la cuenta Alegra los valores posibles de `type`, identificación y estructura de DV antes de automatizar más reglas.
- No se importan automáticamente contactos internos, proveedores ni listas de precios.
- Las credenciales no estaban disponibles en este entorno, por lo que no se realizó una nueva llamada real a `/contacts`; el mapeo de campos se basa en el diagnóstico previamente conectado y en el cliente read-only existente.
- Crédito, plazo, vendedor y cartera siguen siendo información externa de referencia, no reglas activas de Betta.

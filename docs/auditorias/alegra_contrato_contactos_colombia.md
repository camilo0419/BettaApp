# Auditoría contractual de contactos Alegra Colombia

Fecha: 2026-10-12  
Alcance: diagnóstico de solo lectura. No se ejecutaron POST, PUT, PATCH ni DELETE contra Alegra.

## 1. Resumen ejecutivo

BettaApp utiliza correctamente la familia de endpoints v1 documentada para contactos:

- `GET /contacts` para listados paginados.
- `GET /contacts/{id}` para detalle.
- `POST /contacts` para altas, actualmente protegido por autorización explícita.
- `PUT /contacts/{id}` para actualizaciones, actualmente protegido por autorización explícita.

La documentación oficial confirma Basic Auth, `limit` máximo 30 en listados y los catálogos colombianos de tipos de identificación, regímenes, tipos de persona, responsabilidades fiscales y nombres de departamentos/municipios.

La documentación pública no expone de forma completa el esquema colombiano de contacto ni confirma expresamente la semántica de actualización parcial de `PUT`. Por tanto, no es válido declarar que todos los campos enviados por BettaApp están contractualmente confirmados solo por aparecer en respuestas observadas o en ejemplos de código.

Conclusión: la integración está preparada técnicamente para consultas y para preparar operaciones salientes, pero la habilitación productiva de PUT debe seguir condicionada a una validación controlada del contrato colombiano y de los campos tributarios/geográficos. El código no debe modificar automáticamente identificación, tipo de persona o régimen como si fueran datos comerciales ordinarios.

## 2. Documentación oficial consultada

- [Documentación general de Alegra](https://developer.alegra.com/).
- [Crear contacto](https://developer.alegra.com/reference/post_contacts): `POST /api/v1/contacts`.
- [Editar contacto](https://developer.alegra.com/reference/editcontact): `PUT /api/v1/contacts/{id}`.
- [Obtener contacto](https://developer.alegra.com/reference/contactsdetails-1): `GET /api/v1/contacts/{id}`.
- [Listado de contactos](https://developer.alegra.com/reference/listcontacts-1): `GET /api/v1/contacts`.
- [Catálogo Colombia](https://developer.alegra.com/reference/colombia).
- [Autenticación](https://developer.alegra.com/reference/autenticaci%C3%B3n).
- [Códigos de respuesta](https://developer.alegra.com/reference/respuestas-del-api-y-manejo-de-errores).

### Evidencia contractual confirmada

La página de listado documenta `start`, `limit`, `metadata`, `order_direction`, `order_field`, `query`, `identification`, `name`, `type` y `mode`. El límite máximo documentado es 30. `metadata` incluye el total.

La página de Colombia documenta, entre otros:

- Identificaciones: `RC`, `TI`, `CC`, `TE`, `CE`, `PP`, `PEP`, `DIE`, `NUIP`, `FOREIGN_NIT`.
- Regímenes: `SIMPLIFIED_REGIME`, `COMMON_REGIME`, `NOT_REPONSIBLE_FOR_CONSUMPTION`, `SPECIAL_REGIME`, `NATIONAL_CONSUMPTION_TAX`, `INC_IVA_RESPONSIBLE`.
- Personas: `LEGAL_ENTITY`, `PERSON_ENTITY`.
- Responsabilidades fiscales: `5`, `7`, `12`, `114`.
- Geografía presentada como nombres de departamentos y municipios, no como códigos DIVIPOLA.

### No confirmado por la documentación pública

- Que `identificationObject`, `kindOfPerson`, `regime` y `nameObject` sean obligatorios en todos los POST.
- Que esos mismos campos sean obligatorios en todos los PUT.
- Que PUT sea un PATCH semántico y conserve siempre todos los campos omitidos.
- Que se acepte simultáneamente `name` y `nameObject` en el mismo request colombiano.
- El nombre exacto del atributo para responsabilidades fiscales dentro del payload de contacto.
- Un endpoint público de Alegra para resolver códigos DIVIPOLA o devolver identificadores geográficos.
- Las reglas exactas para `null`, cadena vacía y eliminación de atributos en PUT.

Estas cuestiones requieren confirmación documental adicional o una prueba controlada autorizada; no deben resolverse mediante ensayo y error sobre clientes reales.

## 3. Matriz contractual de campos

Las columnas “POST” y “PUT” reflejan únicamente lo explícitamente documentado. “No indicado” significa que la documentación consultada no permite afirmar obligatoriedad.

| Campo API | Tipo/formato documentado | POST | PUT | GET | Campo BettaApp | Transformación | Riesgo |
|---|---|---|---|---|---|---|---|
| `name` | string | Obligatorio | Obligatorio según ficha PUT | string | `nombre`, `razon_social` | Empresa usa razón social; persona usa nombre estructurado internamente | Alto si se omite o se envía vacío |
| `nameObject` | objeto de nombres | NO CONFIRMADO en tabla pública | NO CONFIRMADO | Observado en respuestas colombianas | `primer_nombre`, apellidos | Construcción de nombres separados | Alto: no está descrito en el esquema genérico |
| `identification` | string | No marcado obligatorio en ficha | No marcado obligatorio en ficha | string | `identificacion` | Texto normalizado conservando valor comercial | Alto; errores observados 2006/2035 |
| `identificationObject` | objeto | NO CONFIRMADO en tabla genérica | NO CONFIRMADO | Observado en GET avanzado | `tipo_identificacion`, `identificacion`, `digito_verificacion` | Mapea códigos locales a códigos Alegra | Crítico para identidad tributaria |
| `identificationObject.type` | catálogo colombiano | Catálogo confirmado, obligatoriedad de objeto NO CONFIRMADA | Igual | parte del objeto | `tipo_identificacion` | `cc`→`CC`, `nit`→`NIT`, `ce`→`CE`, pasaporte→`PP` | Alto por códigos no cubiertos localmente |
| `identificationObject.number` | string | NO CONFIRMADO separadamente | NO CONFIRMADO | parte del objeto | `identificacion` | Sin inferir tipo por número | Crítico |
| `identificationObject.dv` | string de un dígito para NIT, observado | NO CONFIRMADO | NO CONFIRMADO | presente en algunos GET | `digito_verificacion` | Conserva `0`; se excluye si está vacío | Alto |
| `kindOfPerson` | enum Colombia | Catálogo confirmado, obligatoriedad no confirmada | No confirmada | GET avanzado | `tipo_cliente` | empresa→`LEGAL_ENTITY`, persona→`PERSON_ENTITY` | Crítico; no inferir por NIT |
| `regime` | enum Colombia | Catálogo confirmado, obligatoriedad no confirmada | No confirmada | GET avanzado | `regimen_tributario` | Conserva código explícito | Crítico |
| `type` | enum/string cliente/proveedor/ambos | Enum documentado | Enum documentado | string o lista observada | implícito cliente | BettaApp envía `client` | Medio; verificar si PUT admite string y GET devuelve lista |
| `status` | `active`/`inactive` | Enum documentado | Enum documentado | string | `activo` | booleano→estado | Medio |
| `email` | string/email | Opcional según ficha | Opcional según ficha | string | `email` | Solo se envía no vacío | Bajo/medio |
| `phonePrimary` | string | Opcional según ficha | Opcional según ficha | string | `telefono` | Texto | Bajo |
| `phoneSecondary` | string | Opcional según ficha | Opcional según ficha | string | `telefono_secundario` | Texto | Bajo |
| `mobile` | string | Opcional según ficha | Opcional según ficha | string | `celular` | Texto | Bajo |
| `address` | objeto | Opcional según ficha | Opcional según ficha | objeto | dirección/geografía | Subobjeto con texto | Alto si se reemplaza parcialmente |
| `address.address` | string | NO CONFIRMADO | NO CONFIRMADO | subcampo | `direccion` | Texto | Medio |
| `address.city` | string | NO CONFIRMADO | NO CONFIRMADO | subcampo | `ciudad`/DIVIPOLA | Nombre oficial del catálogo | Alto por diferencias de acentos/nombres |
| `address.department` | string | NO CONFIRMADO | NO CONFIRMADO | subcampo | `departamento`/DIVIPOLA | Nombre oficial del catálogo | Alto |
| `address.country` | string | NO CONFIRMADO | NO CONFIRMADO | subcampo | `pais` | Actualmente `Colombia` | Medio |
| `address.zipCode` | string | NO CONFIRMADO | NO CONFIRMADO | subcampo | `codigo_postal` | Texto | Medio |
| `seller` | string ID | Opcional | Opcional | objeto/string según respuesta | No equivalente | No enviado | Bajo: se conserva remoto al no enviarse, si PUT es parcial |
| `priceList` | string ID | Opcional | Opcional | objeto/string según respuesta | No equivalente | No enviado | Bajo/medio |
| `term` | string ID | Opcional | Opcional | objeto/string según respuesta | No equivalente | No enviado | Bajo/medio |
| `creditLimit` | number | Opcional | Opcional | number | No equivalente | No enviado | Bajo |
| `fax` | string | Opcional | Opcional | string | No equivalente seguro | No enviado | Bajo |
| `accounting` | objeto | Opcional | Opcional | objeto | No equivalente | No enviado | Alto si PUT reemplaza objeto completo |
| `internalContacts` | array/null | Opcional | Opcional | array/null | No equivalente | No enviado | Alto si PUT reemplaza colección |
| `ignoreRepeated` | boolean | Opcional | Opcional | respuesta no central | No equivalente | Solo control de alta | Medio |
| `statementAttached` | `yes`/`no` | Opcional | Opcional | string | No equivalente | No enviado | Bajo |
| Responsabilidades fiscales | Atributo exacto no confirmado en contacto | NO CONFIRMADO | NO CONFIRMADO | catálogo separado | No existe campo equivalente | No se puede fabricar | Crítico para e-factura |
| `seller`, `priceList`, `term` | referencias externas | Opcional | Opcional | incluidos en advanced | No administrados localmente | Se conservan solo si PUT parcial está confirmado | Medio |

## 4. Contratos POST, GET y PUT

### POST

La ficha oficial de crear contacto documenta `name` como obligatorio y enumera identificación, dirección, teléfonos, vendedor, lista de precios, plazo, límite de crédito, email, tipo, estado, fax, contabilidad, contactos internos, `ignoreRepeated` y `statementAttached`.

La implementación local agrega el esquema colombiano:

- `identificationObject`.
- `kindOfPerson`.
- `regime`.
- `type: client`.
- `status`.
- `nameObject` para persona natural o `name` para empresa.

La documentación pública consultada no presenta esos campos colombianos dentro de la tabla genérica de POST. El código local exige explícitamente régimen, tipo de persona e identificación; esto es una decisión conservadora de BettaApp, no una obligación completamente demostrada por la página pública.

### GET

`GET /contacts/{id}` está documentado para recuperar el detalle. `GET /contacts` permite filtros por identificación/nombre, tipo y modo `advanced`; `metadata` permite recuperar el total. El modo avanzado es necesario para campos no incluidos en la respuesta simple.

BettaApp usa `AlegraReadOnlyClient` en [tienda/services/alegra_client.py](../../tienda/services/alegra_client.py), con Basic Auth, `limit` máximo 30, `start`, `metadata=true`, detección de páginas repetidas y validación del total declarado.

### PUT

La ficha oficial de editar contacto documenta `name` como obligatorio y los mismos campos generales de contacto, con respuestas `201`, `400` y `404`. No declara que todos los demás campos sean obligatorios ni afirma explícitamente que el request sea parcial.

BettaApp construye el PUT en `AlegraContactWriteService._build_update_payload()` y siempre añade contexto tributario obtenido del GET:

```text
identification
identificationObject
kindOfPerson
regime
type
status
```

Después agrega solo campos locales autorizables, como nombre, email, teléfonos o dirección. Esta estrategia evita enviar cambios tributarios locales bloqueados, pero presupone que Alegra acepta el contexto tributario en cada PUT. La presuposición debe validarse con contrato colombiano o prueba controlada.

#### Preguntas PUT pendientes de confirmación

| Pregunta | Estado |
|---|---|
| ¿PUT conserva campos omitidos? | NO CONFIRMADO |
| ¿PUT acepta `nameObject` junto con `name`? | NO CONFIRMADO; BettaApp evita enviarlos juntos para persona natural salvo cambios de nombre |
| ¿PUT requiere `identificationObject`? | NO CONFIRMADO documentalmente; el código lo exige por seguridad |
| ¿PUT requiere `kindOfPerson` y `regime`? | NO CONFIRMADO documentalmente; el código los exige para evitar payload incompleto |
| ¿Cadena vacía elimina o invalida un campo? | NO CONFIRMADO; BettaApp omite valores vacíos |
| ¿Un PUT comercial puede modificar identidad tributaria? | No debe permitirse por política interna, aunque el endpoint pueda aceptarlo |

Los errores `2001` (nombre obligatorio), `2035` (tipo de identificación obligatorio) y `2006` (identificación duplicada) deben tratarse como evidencia observada de validaciones del servidor, no como sustituto de la documentación contractual. No se recomienda repetir PUT ciegamente.

## 5. Catálogo geográfico

La página oficial de Colombia lista departamentos y municipios por nombre, incluyendo `Bogotá D.C.` y `Bogotá, D.C.` en representaciones diferentes. No documenta códigos DIVIPOLA, IDs geográficos propios ni un endpoint de municipios para contactos.

La implementación local contiene:

- `ColombiaMunicipio`: catálogo local versionado con código DIVIPOLA, departamento, municipio y estado.
- `Cliente.divipola_municipio`: FK opcional.
- `tienda/services/geografia.py`: búsqueda local y adaptación a `{city, department, country}`.
- `panel_ajax_divipola`: endpoint autenticado de consulta local.

### Compatibilidad

DIVIPOLA puede transformarse inequívocamente a una pareja local de código y nombre, pero no se demostró que Alegra acepte o almacene el código DIVIPOLA. BettaApp actualmente envía nombres, no códigos. La equivalencia semántica local elimina diferencias como `Medellin`/`Medellín`, pero no prueba que todas las denominaciones oficiales de DANE sean aceptadas literalmente por Alegra.

Riesgo concreto: Bogotá y denominaciones especiales pueden tener diferencias de puntuación, abreviatura o mayúsculas. Debe existir una tabla de equivalencias explícita o confirmación contractual antes de habilitar PUT de ubicación masivo.

## 6. Auditoría del código BettaApp

### Flujo actual

1. `cliente_crear` guarda el cliente y llama `enqueue_create()` si corresponde.
2. `cliente_editar` determina si existe `ExternalObjectMap` activo y llama `enqueue_update_if_changed()` o `enqueue_create()`.
3. `enqueue_update_if_changed()` exige un baseline confirmado, recupera el contacto por GET y prepara un `AlegraWriteOperation`.
4. `alegra_procesar_operaciones` usa bloqueo advisory, procesa pendientes y requiere `--execute`, confirmación y dos variables de entorno.
5. `execute_update()` marca la operación `sent`, ejecuta PUT, confirma con GET y actualiza el mapeo/baseline.
6. Si hay conflicto tributario, baseline ausente, múltiples mapas o contexto remoto incompleto, la operación se bloquea.

### Protecciones confirmadas

- `ExternalObjectMap` separa sistema, recurso, ID externo y objeto local.
- La preparación de factura exige exactamente un mapa activo para cliente y productos.
- La cola evita POST si ya existe un mapa, candidato remoto, operación incierta o error preventivo no resuelto.
- Las escrituras requieren autorización acotada, `ALEGRA_EXTERNAL_WRITES_ENABLED=true` y, para automatización, `ALEGRA_AUTOMATION_WRITES_ENABLED=true`.
- El cron usa `advisory_lock()`.
- Los errores HTTP 429/5xx y resultados inciertos no se consideran éxito.

### Incompatibilidades o brechas

| Severidad | Archivo/función | Hallazgo | Estado |
|---|---|---|---|
| Crítica | `tienda/services/alegra_write.py:236`, `build_contact_payload` | El código exige régimen y persona, pero la página pública de POST no confirma expresamente esa obligatoriedad colombiana | Requiere aprobación contractual |
| Crítica | `tienda/services/alegra_write.py:438`, `_build_update_payload` | El PUT envía contexto fiscal completo aunque la semántica de PUT parcial no está documentada | Requiere prueba controlada |
| Alta | `tienda/services/geografia.py:40`, `alegra_location` | Se envían nombres DIVIPOLA sin ID Alegra; no hay contrato público de códigos geográficos | Requiere equivalencias |
| Alta | `tienda/forms.py:486`, `clean_divipola_code` | La selección usa código local y completa texto local; no valida aceptación exacta del nombre por Alegra | Requiere catálogo de equivalencias |
| Alta | `tienda/models.py:587`, `Cliente.tipo_identificacion` | El catálogo local no representa todos los códigos oficiales (`RC`, `TI`, `TE`, `PEP`, `DIE`, `NUIP`, `FOREIGN_NIT`) | Riesgo de rechazo o pérdida |
| Media | `tienda/services/alegra_invoice_prepare.py` | La preparación local valida mapas e impuestos, pero no prueba contrato de emisión electrónica | Fuera de emisión; pendiente |
| Media | `tienda/models.py:603`, `Cliente.regimen_tributario` | No existe un campo de responsabilidades fiscales separado | Debe resolverse antes de e-facturación si Alegra lo exige |

## 7. Caso Diego

Escenario analizado solo con código y fixtures, sin producción:

- Cliente local 3.
- Contacto externo 479.
- Mapa externo 173.
- Persona natural, CC, `PERSON_ENTITY`, `COMMON_REGIME`.
- Medellín / Antioquia / Colombia.

Con la normalización geográfica actual, diferencias de acentos, mayúsculas y espacios se comparan semánticamente. Por tanto, `Medellín` frente a `Medellin` no debería mantener el caso en `REVIEW` si los demás campos coinciden.

Una diferencia real de ciudad, departamento o país debe continuar en `REVIEW`. No debe inicializarse baseline ni ejecutar PUT para resolverla automáticamente.

No se reactivaron las operaciones fallidas ni se consultó producción.

## 8. Facturación electrónica

Crear un contacto, crear una factura y emitirla electrónicamente son estados diferentes.

La documentación Colombia identifica estados legales como `PENDING`, `STAMPED_AND_ACCEPTED`, `STAMPED_AND_REJECTED` y otros, pero la preparación actual de BettaApp es local y no ejecuta POST de factura.

Antes de habilitar facturación electrónica se debe confirmar en la documentación del endpoint de facturas:

- Cliente externo y datos fiscales requeridos.
- Productos, cantidades, precios, descuentos e impuestos.
- Responsabilidades fiscales y su atributo exacto.
- Forma y medio de pago.
- Numeración/resolución.
- Estado de emisión electrónica.
- Envío de correo y documento PDF/XML.
- Consulta posterior y manejo de rechazo DIAN.

La preparación existente en `tienda/services/alegra_invoice_prepare.py` exige venta confirmada, un mapa activo inequívoco de cliente y productos en modalidad detallada, y bloquea impuestos sin equivalencia externa confirmada. Esto es correcto como preparación conservadora, pero no demuestra emisión electrónica.

## 9. Riesgos clasificados

### Críticos

1. Habilitar PUT sin confirmar si el contrato colombiano requiere siempre campos fiscales completos.
2. Modificar identificación, DV, régimen o tipo de persona como si fueran cambios comerciales.
3. Tratar `regime` como sustituto de responsabilidades fiscales.
4. Crear/actualizar un contacto sin verificar duplicados por identificación.

### Altos

1. Diferencias de nombres geográficos entre DANE y Alegra.
2. Tipos de identificación locales más limitados que el catálogo oficial de Alegra.
3. Baselines antiguos que no contengan todos los campos protegidos.
4. Presuponer que un PUT parcial conserva atributos no enviados.

### Medios

1. Campos de vendedor, lista de precios, plazo y contabilidad no administrados por BettaApp.
2. Falta de campo local para responsabilidades fiscales.
3. Diferencias entre listas en GET y strings en payload.

## 10. Plan de corrección priorizado

### P0 — Antes de cualquier escritura real

1. Obtener del contrato colombiano de Alegra un ejemplo normativo completo de POST y PUT.
2. Confirmar obligatoriedad y semántica de `identificationObject`, `kindOfPerson`, `regime`, `nameObject` y responsabilidades fiscales.
3. Confirmar si PUT parcial conserva campos no enviados y cómo trata vacíos.
4. Confirmar la representación exacta de ciudad/departamento/país y el caso Bogotá D.C.

### P1 — Modelo y validación local

1. Decidir si BettaApp debe almacenar responsabilidades fiscales como campo/relación separada.
2. Ampliar tipos de identificación solo con catálogo oficial confirmado.
3. Introducir una tabla de equivalencias geográficas únicamente si el contrato exige valores distintos a DIVIPOLA.
4. Mantener los campos tributarios fuera de actualizaciones automáticas ordinarias.

### P2 — Transporte y operación

1. Mantener POST/PUT desactivados por defecto.
2. Habilitar primero un único contacto de prueba no comercial, con respaldo y conciliación posterior.
3. Verificar respuesta GET y estado de operación antes de considerar sincronizado.
4. Ejecutar pruebas de timeout, 400, 401, 429, 5xx, duplicado y respuesta incierta.

### P3 — Facturación

1. Auditar el contrato de factura de venta Colombia.
2. Completar responsabilidades fiscales, impuestos, pago y numeración.
3. Separar creación, emisión, aceptación DIAN y envío de correo.

## 11. Pruebas necesarias antes del despliegue

- POST simulado con persona natural, empresa, NIT con DV `0`, CC y campos vacíos.
- PUT comercial sin modificación tributaria.
- PUT con identificación/régimen/tipo de persona bloqueado.
- PUT con respuesta 400/401/429/500/503.
- GET avanzado con `type` como lista y con campos fiscales incompletos.
- Paginación con total declarado, página vacía y página repetida.
- Duplicidad por identificación, coincidencia ambigua y ausencia confirmada.
- Medellín/Medellin, Bogotá D.C. y municipios con nombres compuestos.
- Preservación de vendedor, lista de precios, plazo y contabilidad cuando BettaApp no los administra.
- Baseline confirmado, baseline ausente y baseline incompleto.
- Preparación de factura antes y después de tener mapas activos.
- Prueba MariaDB real antes de activar el cron productivo.

## 12. Decisiones que requieren aprobación

1. Si BettaApp administrará responsabilidades fiscales o solo régimen.
2. Si se autoriza modificación automática de ubicación comercial.
3. Si el contrato oficial permite PUT parcial o exige payload completo.
4. Qué campos tributarios se consideran inmutables después de facturación.
5. Qué contacto de prueba y qué ventana de habilitación se usarán para la primera escritura real.

## Veredicto

**NO APTO para activar escrituras automáticas basándose únicamente en esta auditoría.**

**APTO para continuar consultas GET, preparación local, pruebas simuladas y revisión contractual.**

La integración existente no debe declararse plenamente compatible con el contrato colombiano hasta resolver las preguntas marcadas como NO CONFIRMADO.

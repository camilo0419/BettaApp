# Diagnóstico inicial de integración con Alegra API

Generado: `2026-10-08T00:20:56.966520+00:00`. Alcance: solo lectura; límite por recurso: `30`.

## Resultado de conexión

- Estado: `connected`.
- No se registran credenciales, tokens, encabezados Authorization ni datos personales completos.

## Endpoints y resultados

| Recurso | Resultado | HTTP | Registros detectados | Estructura depurada |
|---|---|---:|---:|---|
| `empresa` | `ok` | `200` | `—` | `[{"name": "str", "identification": "str", "phone": "str", "website": "str", "email": "str", "regime": "str", "regimeKey": "str", "applicationVersion": "str", "registryDate": "str", "timezone": "str", "profile": "str", "decimalPrecision": "str", "calculationScale": "int", "multitax": "bool", "employeesNumber": "str", "sector": "str", "showInvoiceTotalInWords": "str", "showRetentionInvoice": "str", "showNewLineCharOnPdf": "str", "showItemReferenceOnPdf": "bool", "additionalChargesActive": "bool", ` |
| `items` | `ok` | `[200]` | `30` | `[{"id": "str", "category": {"id": "str", "name": "str"}, "hasNoIvaDays": "bool", "name": "str", "description": "null", "reference": "str", "status": "str", "calculationScale": "int", "price": [{"idPriceList": "str", "name": "str", "type": "str", "price": "int", "currency": {"code": "str", "symbol": "str"}, "main": "bool", "edited": "bool"}], "tax": [], "customFields": [], "productKey": "null", "type": "str", "itemType": "null"}]` |
| `categorias_alegra` | `ok` | `[200]` | `6` | `[{"id": "str", "name": "str", "description": "null", "status": "str"}]` |
| `listas_precios` | `ok` | `[200]` | `3` | `[{"id": "str", "name": "str", "description": "str", "status": "str", "type": "str", "main": "bool", "currency": {"code": "str", "symbol": "str"}}]` |
| `bodegas` | `ok` | `[200]` | `2` | `[{"id": "str", "costCenter": "null", "name": "str", "status": "str", "isDefault": "bool"}]` |
| `impuestos` | `ok` | `[200]` | `0` | `[]` |
| `clientes_contactos` | `ok` | `[200]` | `30` | `[{"id": "str", "uuid": "str", "name": "str", "identification": "str", "phonePrimary": "null", "phoneSecondary": "null", "mobile": "str", "email": "str", "status": "str", "type": ["str"], "address": {"zipCode": "null", "department": "str", "country": "str", "address": "str", "city": "str"}, "term": "null", "seller": "null", "priceList": "null", "statementAttached": "bool", "fax": "null", "observations": "null", "accounting": {"accountReceivable": {"id": "str", "idParent": "str", "name": "str", "t` |

Los inventarios se diagnostican dentro de `/items` en modo `advanced`, pues la API expone allí la información de inventario por ítem y bodega; no se ejecutó una operación de escritura.

## BettaApp inspeccionado

- Modelos y cantidades: `{"models": {"Producto": {"count": 5, "fields": ["nombre", "categoria_id", "descripcion_corta", "descripcion_larga", "activo", "tipo_calculo", "precio_base_m2", "precio_base_unidad"]}, "Categoria": {"count": 7, "fields": ["nombre", "slug", "activa"]}, "Cliente": {"count": 2, "fields": ["tipo_cliente", "nombre", "razon_social", "identificacion", "email", "telefono", "direccion", "ciudad", "activo"]}, "ClientePuntoVenta": {"count": 0, "fields": ["cliente_id", "nombre", "direccion", "ciudad", "contacto", "telefono", "email", "activo"]}}, "missing_domain_models": ["precio/lista de precios", "inventario", "bodega", "impuesto", "identificador externo Alegra"]}`

## Compatibilidad, riesgos y propuesta

- Equivalencias y brechas: `["Alegra item.name/description ↔ Producto.nombre/descripcion_*.", "Alegra item.category ↔ Producto.categoria, pero requiere tabla de equivalencias por id externo.", "Alegra contact ↔ Cliente; identificación/email son candidatos de conciliación, no suficientes por sí solos.", "Alegra contact internalContacts ↔ ClienteContacto y posiblemente ClientePuntoVenta, con revisión semántica.", "Alegra item.price ↔ precios Betta; Betta tiene dos precios calculados, no listas de precio normalizadas.", "Alegra inventory.warehouse ↔ no hay inventario/bodega equivalente en Betta.", "Alegra tax ↔ no hay impuesto equivalente en Betta."]`
- Riesgos: Variantes, combos y subitems no tienen representación directa en Producto/ProductoCampo. Una categoría local y una categoría de ítem de Alegra son dominios distintos; no asumir igualdad por nombre. Se necesita alegra_id por recurso y marca de origen/última sincronización para evitar duplicados. Definir sistema maestro por campo, idempotencia y estrategia de conflictos antes de CRUD bidireccional. Las respuestas avanzadas pueden incluir estructura variable por país, permisos y configuración de la cuenta.
- Arquitectura propuesta: Servicio aislado AlegraReadOnlyClient → adaptadores por recurso → capa de conciliación → persistencia de mapeos externos. Cola/outbox e idempotency keys para la futura escritura; no implementar en esta etapa. Webhook receptor y sincronización incremental con auditoría para el futuro.
- Recomendación inicial: Primero importar en modo staging y generar un reporte de coincidencias por identificación/referencia/nombre; aprobar manualmente el mapeo antes de crear o actualizar datos.

## Próximos pasos CRUD y webhooks

- Confirmar recursos/campos reales de la cuenta y reglas de negocio para variantes, bodegas, impuestos y precios.
- Diseñar modelos de integración/mapeos externos sin alterar los modelos actuales hasta aprobar el contrato.
- Definir permisos, reintentos, rate limit, webhooks y política de conflictos para CRUD.

## Documentación oficial consultada

- https://developer.alegra.com/reference/get_company-1
- https://developer.alegra.com/reference/get_items
- https://developer.alegra.com/reference/get_item-categories
- https://developer.alegra.com/reference/get_price-lists
- https://developer.alegra.com/reference/get_warehouses
- https://developer.alegra.com/reference/get_taxes
- https://developer.alegra.com/reference/listcontacts-1

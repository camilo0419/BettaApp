# BettaApp — Validación real de Alegra

Fecha de ejecución: `2026-10-08T05:41:56.364222+00:00`.

## Entorno y seguridad

- Settings: `config.settings`.
- Base de datos: SQLite local (`C:\Users\camil\OneDrive\Escritorio\Python Scripts\betta_diseno_mvp\db.sqlite3`).
- El diagnóstico trabajó únicamente en memoria: no creó staging, mapeos, notificaciones ni cambios financieros.
- Cliente externo: únicamente GET; no se registran credenciales, tokens ni Authorization.
- Límite por recurso: `5`; una muestra limitada no se interpreta como total de la cuenta.

## Resultados por recurso

| Recurso | Endpoint | Resultado | HTTP | Registros | Páginas |
|---|---|---|---|---:|---:|
| productos | `/items` | `ok` | `[200]` | `5` | `1` |
| clientes | `/contacts` | `ok` | `[200]` | `5` | `1` |
| facturas | `/invoices` | `ok` | `[200]` | `5` | `1` |
| pagos | `/payments` | `ok` | `[200]` | `5` | `1` |

### productos

```json
{
  "types": {
    "product": 5
  },
  "with_name": 5,
  "with_reference": 5,
  "with_category": 5,
  "with_price": 5,
  "with_inventory": 0,
  "with_variants": 0
}
```

Estructura observada (tipos, no valores personales): `[{'id': 'str', 'category': {'id': 'str', 'name': 'str'}, 'hasNoIvaDays': 'bool', 'name': 'str', 'description': 'null', 'reference': 'str', 'status': 'str', 'calculationScale': 'int', 'price': [{'idPriceList': '…', 'name': '…', 'type': '…', 'price': '…', 'currency': '…', 'main': '…', 'edited': '…'}], 'tax': [], 'customFields': [], 'productKey': 'null', 'type': 'str', 'itemType': 'null'}]`.

### clientes

```json
{
  "with_name": 5,
  "with_identification": 5,
  "duplicate_identifications_in_sample": 0,
  "with_email": 5,
  "with_address": 5,
  "with_branch_offices": 0,
  "with_internal_contacts": 0,
  "types": {
    "client": 5
  }
}
```

Estructura observada (tipos, no valores personales): `[{'id': 'str', 'uuid': 'str', 'name': 'str', 'identification': 'str', 'phonePrimary': 'null', 'phoneSecondary': 'null', 'mobile': 'str', 'email': 'str', 'status': 'str', 'type': ['str'], 'address': {'zipCode': 'null', 'department': 'str', 'country': 'str', 'address': 'str', 'city': 'str'}, 'term': 'null', 'seller': 'null', 'priceList': 'null', 'statementAttached': 'bool', 'fax': 'null', 'observations': 'null', 'accounting': {'accountReceivable': {'id': '…', 'idParent': '…', 'name': '…', 'text': '…', 'code': '…', 'description': '…', 'type': '…', 'readOnly': '…', 'nature': '…', 'blocked': '…', 'status': '…', 'categoryRule': '…', 'use': '…', 'showThirdPartyBalance': '…', 'idGlobal': '…', 'behavior': '…'}, 'debtToPay': {'id': '…', 'idParent': '…', 'name': '…', 'text': '…', 'code': '…', 'description': '…', 'type': '…', 'readOnly': '…', 'nature': '…', 'blocked': '…', 'status': '…', 'categoryRule': '…', 'use': '…', 'showThirdPartyBalance': '…', 'idGlobal': '…', 'behavior': '…'}}, 'created_at': 'str', 'updated_at': 'str', 'branchOffices': [], 'attachmentsTotal': 'int', 'creditLimit': 'null', 'identificationObject': {'dv': 'str', 'type': 'str', 'number': 'str'}, 'kindOfPerson': 'str', 'regime': 'str', 'fiscalResponsabilities': [], 'settings': {'sendElectronicDocuments': 'bool'}, 'enableHealthSector': 'bool', 'healthPatients': 'null', 'fiscalResidence': 'null', 'formulario300Classification': 'null'}]`.

### facturas

```json
{
  "statuses": {
    "closed": 5
  },
  "currencies": {
    "missing": 5
  },
  "with_issue_date": 5,
  "with_due_date": 5,
  "with_total": 5,
  "with_balance": 5,
  "sample_total_sum": "2230774",
  "sample_balance_sum": "0",
  "financial_eligibility": {
    "eligible": 5
  },
  "billing_eligibility": {
    "eligible": 5
  },
  "overdue_count": 0,
  "aging": {
    "no_aplica_sin_saldo": 5
  }
}
```

Estructura observada (tipos, no valores personales): `[{'id': 'str', 'date': 'str', 'dueDate': 'str', 'datetime': 'str', 'observations': 'str', 'anotation': 'str', 'termsConditions': 'str', 'status': 'str', 'client': {'id': 'str', 'name': 'str', 'identification': 'str', 'phonePrimary': 'str', 'phoneSecondary': 'str', 'fax': 'str', 'mobile': 'str', 'email': 'str', 'regime': 'str', 'identificationType': 'str', 'address': {'address': '…', 'department': '…', 'city': '…'}, 'branchOffice': 'null', 'kindOfPerson': 'str', 'identificationObject': {'type': '…', 'number': '…'}}, 'numberTemplate': {'id': 'str', 'prefix': 'str', 'number': 'str', 'text': 'null', 'documentType': 'str', 'fullNumber': 'str', 'formattedNumber': 'str', 'isElectronic': 'bool'}, 'subtotal': 'int', 'discount': 'int', 'tax': 'int', 'total': 'int', 'totalPaid': 'int', 'balance': 'int', 'decimalPrecision': 'str', 'term': 'str', 'warehouse': {'id': 'str', 'name': 'str'}, 'originApp': 'str', 'type': 'str', 'paymentForm': 'str', 'barCodeContent': 'str', 'seller': 'null', 'priceList': {'id': 'str', 'name': 'str'}, 'payments': [{'id': '…', 'prefix': '…', 'number': '…', 'date': '…', 'amount': '…', 'paymentMethod': '…', 'observations': '…', 'anotation': '…', 'status': '…', 'createdAt': '…', 'updatedAt': '…'}], 'items': [{'name': '…', 'description': '…', 'price': '…', 'discount': '…', 'reference': '…', 'quantity': '…', 'id': '…', 'productKey': '…', 'unit': '…', 'tax': '…', 'total': '…'}], 'costCenter': 'null', 'printingTemplate': {'id': 'str', 'name': 'str', 'pageSize': 'str', 'supportedPdfEngine': 'null'}}]`.

### pagos

```json
{
  "statuses": {
    "open": 5
  },
  "currencies": {
    "missing": 5
  },
  "with_date": 5,
  "with_amount": 5,
  "sample_amount_sum": "2221400",
  "with_client": 5,
  "without_invoice_reference": 0,
  "one_invoice_reference": 5,
  "multiple_invoice_references": 0,
  "reliable_invoice_application": true
}
```

Estructura observada (tipos, no valores personales): `[{'id': 'str', 'date': 'str', 'number': 'str', 'amount': 'int', 'observations': 'str', 'anotation': 'str', 'type': 'str', 'paymentMethod': 'str', 'status': 'str', 'decimalPrecision': 'str', 'calculationScale': 'int', 'bankAccount': {'id': 'str', 'name': 'str', 'type': 'str'}, 'client': {'id': 'str', 'name': 'str', 'phone': 'null', 'identification': 'str'}, 'invoices': [{'id': '…', 'number': '…', 'date': '…', 'amount': '…', 'total': '…', 'balance': '…'}], 'costCenter': 'null', 'numberTemplate': {'id': 'str', 'prefix': 'null', 'number': 'str', 'fullNumber': 'str', 'formattedNumber': 'str'}}]`.

## Diferencias y límites

- Los totales y saldos reportados son sumas de la muestra examinada, no totales históricos de la cuenta.
- La elegibilidad financiera se calculó con las reglas actuales de BettaApp sin modificar staging.
- La aplicación de pagos se considera confiable solo cuando la respuesta trae exactamente una factura por pago; múltiples o ninguna referencia requieren conciliación adicional.
- Los estados no reconocidos se mantienen fuera de elegibilidad financiera.
- La muestra de /items no expuso un campo inventory; no se puede confirmar inventario por bodega con esta respuesta.
- La muestra de /items no expuso variantes; no se puede confirmar compatibilidad de variantes.
- La muestra de /contacts no expuso sucursales ni internalContacts; no se derivan puntos de venta automáticamente.
- Las facturas de la muestra no informaron moneda explícita; BettaApp no debe inferirla como dato de cuenta.
- Los pagos de la muestra traen exactamente una referencia de factura cada uno; esto no demuestra la misma estructura para todos los pagos de la cuenta.

## Veredicto

**VALIDACIÓN INCOMPLETA**

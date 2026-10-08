# BettaApp — Validación real de Alegra

Fecha de ejecución: `2026-10-08T05:47:58.282555+00:00`.

## Entorno y seguridad

- Settings: `config.settings`.
- Base de datos: SQLite local (`C:\Users\camil\OneDrive\Escritorio\Python Scripts\betta_diseno_mvp\db.sqlite3`).
- El diagnóstico trabajó únicamente en memoria: no creó staging, mapeos, notificaciones ni cambios financieros.
- Cliente externo: únicamente GET; no se registran credenciales, tokens ni Authorization.
- Límite por recurso: `100`; máximo de páginas: `4`; pausa: `0.0` s.

## Resultados por recurso

| Recurso | Endpoint | Resultado | Cobertura | HTTP | Registros | Páginas |
|---|---|---|---|---|---:|---:|
| productos | `/items` | `ok` | `limited_by_max_pages` | `[200, 200, 200, 200]` | `100` | `4` |
| clientes | `/contacts` | `ok` | `limited_by_max_pages` | `[200, 200, 200, 200]` | `100` | `4` |
| facturas | `/invoices` | `ok` | `limited_by_max_pages` | `[200, 200, 200, 200]` | `100` | `4` |
| pagos | `/payments` | `ok` | `limited_by_max_pages` | `[200, 200, 200, 200]` | `100` | `4` |

### productos

```json
{
  "types": {
    "product": 57,
    "simple": 40,
    "service": 3
  },
  "with_name": 100,
  "with_reference": 49,
  "with_category": 100,
  "with_price": 100,
  "with_inventory": 83,
  "with_variants": 0
}
```

Estructura observada (tipos, no valores personales): `[{'id': 'str', 'category': {'id': 'str', 'name': 'str'}, 'hasNoIvaDays': 'bool', 'name': 'str', 'description': 'null', 'reference': 'str', 'status': 'str', 'calculationScale': 'int', 'price': [{'idPriceList': '…', 'name': '…', 'type': '…', 'price': '…', 'currency': '…', 'main': '…', 'edited': '…'}], 'tax': [], 'customFields': [], 'productKey': 'null', 'type': 'str', 'itemType': 'null'}]`.

### clientes

```json
{
  "with_name": 100,
  "with_identification": 97,
  "duplicate_identifications_in_sample": 1,
  "with_email": 75,
  "with_address": 100,
  "with_branch_offices": 0,
  "with_internal_contacts": 3,
  "types": {
    "client": 100,
    "provider": 3
  }
}
```

Estructura observada (tipos, no valores personales): `[{'id': 'str', 'uuid': 'str', 'name': 'str', 'identification': 'str', 'phonePrimary': 'null', 'phoneSecondary': 'null', 'mobile': 'str', 'email': 'str', 'status': 'str', 'type': ['str'], 'address': {'zipCode': 'null', 'department': 'str', 'country': 'str', 'address': 'str', 'city': 'str'}, 'term': 'null', 'seller': 'null', 'priceList': 'null', 'statementAttached': 'bool', 'fax': 'null', 'observations': 'null', 'accounting': {'accountReceivable': {'id': '…', 'idParent': '…', 'name': '…', 'text': '…', 'code': '…', 'description': '…', 'type': '…', 'readOnly': '…', 'nature': '…', 'blocked': '…', 'status': '…', 'categoryRule': '…', 'use': '…', 'showThirdPartyBalance': '…', 'idGlobal': '…', 'behavior': '…'}, 'debtToPay': {'id': '…', 'idParent': '…', 'name': '…', 'text': '…', 'code': '…', 'description': '…', 'type': '…', 'readOnly': '…', 'nature': '…', 'blocked': '…', 'status': '…', 'categoryRule': '…', 'use': '…', 'showThirdPartyBalance': '…', 'idGlobal': '…', 'behavior': '…'}}, 'created_at': 'str', 'updated_at': 'str', 'branchOffices': [], 'attachmentsTotal': 'int', 'creditLimit': 'null', 'identificationObject': {'dv': 'str', 'type': 'str', 'number': 'str'}, 'kindOfPerson': 'str', 'regime': 'str', 'fiscalResponsabilities': [], 'settings': {'sendElectronicDocuments': 'bool'}, 'enableHealthSector': 'bool', 'healthPatients': 'null', 'fiscalResidence': 'null', 'formulario300Classification': 'null'}]`.

### facturas

```json
{
  "statuses": {
    "closed": 96,
    "void": 4
  },
  "currencies": {
    "missing": 100
  },
  "with_issue_date": 100,
  "with_due_date": 100,
  "with_total": 100,
  "with_balance": 100,
  "sample_total_sum": "48290695",
  "sample_balance_sum": "0",
  "financial_eligibility": {
    "eligible": 96,
    "excluded": 4
  },
  "billing_eligibility": {
    "eligible": 96,
    "excluded": 4
  },
  "overdue_count": 0,
  "aging": {
    "no_aplica_sin_saldo": 100
  }
}
```

Estructura observada (tipos, no valores personales): `[{'id': 'str', 'date': 'str', 'dueDate': 'str', 'datetime': 'str', 'observations': 'str', 'anotation': 'str', 'termsConditions': 'str', 'status': 'str', 'client': {'id': 'str', 'name': 'str', 'identification': 'str', 'phonePrimary': 'str', 'phoneSecondary': 'str', 'fax': 'str', 'mobile': 'str', 'email': 'str', 'regime': 'str', 'identificationType': 'str', 'address': {'address': '…', 'department': '…', 'city': '…'}, 'branchOffice': 'null', 'kindOfPerson': 'str', 'identificationObject': {'type': '…', 'number': '…'}}, 'numberTemplate': {'id': 'str', 'prefix': 'str', 'number': 'str', 'text': 'null', 'documentType': 'str', 'fullNumber': 'str', 'formattedNumber': 'str', 'isElectronic': 'bool'}, 'subtotal': 'int', 'discount': 'int', 'tax': 'int', 'total': 'int', 'totalPaid': 'int', 'balance': 'int', 'decimalPrecision': 'str', 'term': 'str', 'warehouse': {'id': 'str', 'name': 'str'}, 'originApp': 'str', 'type': 'str', 'paymentForm': 'str', 'barCodeContent': 'str', 'seller': 'null', 'priceList': {'id': 'str', 'name': 'str'}, 'payments': [{'id': '…', 'prefix': '…', 'number': '…', 'date': '…', 'amount': '…', 'paymentMethod': '…', 'observations': '…', 'anotation': '…', 'status': '…', 'createdAt': '…', 'updatedAt': '…'}], 'items': [{'name': '…', 'description': '…', 'price': '…', 'discount': '…', 'reference': '…', 'quantity': '…', 'id': '…', 'productKey': '…', 'unit': '…', 'tax': '…', 'total': '…'}], 'costCenter': 'null', 'printingTemplate': {'id': 'str', 'name': 'str', 'pageSize': 'str', 'supportedPdfEngine': 'null'}}]`.

### pagos

```json
{
  "statuses": {
    "open": 100
  },
  "currencies": {
    "missing": 100
  },
  "with_date": 100,
  "with_amount": 100,
  "sample_amount_sum": "51329243",
  "with_client": 100,
  "without_invoice_reference": 0,
  "one_invoice_reference": 78,
  "multiple_invoice_references": 22,
  "reliable_invoice_application": false
}
```

Estructura observada (tipos, no valores personales): `[{'id': 'str', 'date': 'str', 'number': 'str', 'amount': 'int', 'observations': 'str', 'anotation': 'str', 'type': 'str', 'paymentMethod': 'str', 'status': 'str', 'decimalPrecision': 'str', 'calculationScale': 'int', 'bankAccount': {'id': 'str', 'name': 'str', 'type': 'str'}, 'client': {'id': 'str', 'name': 'str', 'phone': 'null', 'identification': 'str'}, 'invoices': [{'id': '…', 'number': '…', 'date': '…', 'amount': '…', 'total': '…', 'balance': '…'}], 'costCenter': 'null', 'numberTemplate': {'id': 'str', 'prefix': 'null', 'number': 'str', 'fullNumber': 'str', 'formattedNumber': 'str'}}]`.

## Conciliación factura–pago

```json
{
  "invoice_records": 100,
  "payment_records": 100,
  "invoice_payment_relations": 94,
  "invoices_with_observed_payment": 91,
  "invoices_without_observed_payment": 9,
  "invoices_with_multiple_payments": 3,
  "payments_with_multiple_invoices": 22,
  "payments_without_invoice_reference": 0,
  "unmatched_invoice_references": 30,
  "duplicate_invoice_ids": 0,
  "duplicate_payment_ids": 0,
  "relations_with_reported_applied_amount": 94,
  "relations_without_reported_applied_amount": 0,
  "reported_applied_amount_sum": "40000388",
  "application_reconstructible_from_response": true,
  "reconstruction_warning": "No se recalculan saldos: pueden existir notas crédito, retenciones, anticipos u otros ajustes no representados."
}
```

La conciliación usa exclusivamente IDs externos y valores de aplicación presentes en las respuestas consultadas. No reconstruye saldos ni presume cobertura completa.

## Diferencias y límites

- Los totales y saldos reportados son sumas de la muestra examinada, no totales históricos de la cuenta.
- La elegibilidad financiera se calculó con las reglas actuales de BettaApp sin modificar staging.
- La aplicación de pagos se considera confiable solo cuando la respuesta trae exactamente una factura por pago; múltiples o ninguna referencia requieren conciliación adicional.
- Los estados no reconocidos se mantienen fuera de elegibilidad financiera.
- La muestra de /items no expuso variantes; no se puede confirmar compatibilidad de variantes.
- La muestra de /contacts expuso internalContacts en 3 registros; no se interpretan automáticamente como puntos de venta.
- Se observaron 1 identificaciones duplicadas dentro de la muestra de clientes; requieren revisión antes de vincular automáticamente.
- Algunos contactos devueltos también tienen tipo provider; se conservan como contactos externos y no se crean proveedores locales.
- Las facturas de la muestra no informaron moneda explícita; BettaApp no debe inferirla como dato de cuenta.
- 22 pagos tienen múltiples facturas relacionadas; no se puede modelar la relación como uno a uno.
- Hay 30 referencias de pago a facturas fuera de la muestra consultada; no se clasifican como inconsistencias contables.
- Hay 9 facturas de la muestra sin pago observado; la cobertura parcial impide concluir que estén impagas.

## Veredicto

**VALIDACIÓN INCOMPLETA**

# BettaApp — Conciliación dirigida real de Alegra

Fecha de ejecución: `2026-10-08T05:56:19.477561+00:00`.

## Entorno

- Django local con SQLite local.
- Procesamiento en memoria; no se modificó staging, mapeos ni datos comerciales.
- Todas las operaciones externas fueron GET.
- Muestra inicial: máximo `100` registros y `4` páginas por recurso; pausa `0.0` s.

## Endpoints y cobertura

- `/invoices` — muestra inicial de facturas.
- `/payments?type=in` — muestra inicial de pagos recibidos.
- `/contacts?type=client&mode=advanced` — reconstrucción de duplicados de clientes.
- `/invoices/{id}` — detalles dirigidos de referencias pendientes.
- Facturas iniciales: `{"records": 100, "pages": 4, "coverage": "limited_by_max_pages"}`.
- Pagos iniciales: `{"records": 100, "pages": 4, "coverage": "limited_by_max_pages"}`.

## Referencias pendientes y recuperación dirigida

```json
{
  "unique": 27,
  "requested": 27,
  "not_requested_by_limit": 0
}
```
```json
{
  "recovered": 27,
  "not_found": 0,
  "errors": 0,
  "incomplete": 0
}
```
Los identificadores externos no se imprimen ni se conservan en el informe.

## Pagos multifactura y facturas con varios pagos

```json
{
  "payments_with_multiple_invoices": 22,
  "exact_applications": 22,
  "explained_by_adjustment": 0,
  "incomplete_applications": 0,
  "unexplained_differences": 0,
  "invoices_with_multiple_payments": 6,
  "duplicate_payment_applications": 0,
  "payment_date_checks_performed": 124,
  "reported_balance_remains_source_of_truth": true,
  "adjustments_not_identified": true
}
```
Las aplicaciones se comparan solo con los importes explícitamente reportados por Alegra. No se reconstruyen saldos ni se presume que las diferencias sean errores contables.

## Facturas anuladas

```json
{
  "observed": 4,
  "payment_references_in_sample": 0,
  "eligible": 0,
  "active_collection": 0,
  "overdue_collection": 0
}
```
La política BettaApp mantiene documentos anulados fuera de facturación elegible, cartera activa y cartera vencida.

## Clientes con identificación repetida

```json
[
  {
    "records": 2,
    "types": [
      "client"
    ],
    "external_ids_distinct": 2
  }
]
```
No se fusionaron contactos ni se modificaron mapeos.

## Verificación de inventario

```json
{
  "writes_to_alegra": false,
  "inventory_movements_created": false,
  "warehouses_as_points_of_sale": false,
  "inventory_changes_prices_or_quotes": false
}
```
La inspección del código confirma que los datos de inventario se depuran para staging y no activan movimientos, precios, cotizaciones ni puntos de venta.

## Casos no verificables y riesgos

- La consulta está limitada a la muestra y al máximo de referencias dirigido; no representa toda la cuenta.
- Referencias fuera de la muestra no se consideran errores contables.
- Ajustes, retenciones, anticipos y notas crédito no se reconstruyen si no están explícitos.
- La compatibilidad completa requiere repetir la validación con cobertura total controlada y revisar los estados restantes.

## Veredicto

**CONCILIACIÓN CON OBSERVACIONES**

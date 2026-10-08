# BettaApp — Importación controlada de clientes Alegra (Fase 7.3)

## Estado: APLICACIÓN NO EJECUTADA

El comando reconstruye el plan con datos actuales. El comportamiento por defecto es `--dry-run`; no se crean clientes ni mapeos en ese modo. Las consultas externas son exclusivamente GET.

## Consulta

- Contactos recuperados: 167.
- Páginas: 6.
- Cobertura: `complete`; motivo: `end_of_pagination`.
- Estados HTTP: [200, 200, 200, 200, 200, 200].
- Errores: 0; reintentos 429: 0.

## Plan simulado

- `CREATE_LOCAL`: 156.
- `LINK_EXISTING`: 0; no se vincula automáticamente.
- `REVIEW_DUPLICATE`: 8; excluidos.
- `REVIEW_CONFLICT`: 0; bloquean `--apply`.
- `SKIP_INVALID`: 3; excluidos.
- `NO_ACTION`: 0.

No se incluyen nombres, documentos, correos, teléfonos ni payloads completos.

## Protecciones

- `--apply` requiere `--confirm "IMPORTAR CLIENTES LOCALMENTE"`.
- Requiere SQLite local en `db.sqlite3`, cobertura completa, sin errores ni conflictos estructurales.
- Cada alta y su `ExternalObjectMap` se ejecutan en una transacción atómica.
- Un mapeo activo existente produce `NO_ACTION`; no se crean duplicados.
- Errores de validación o unicidad revierten la alta del contacto afectado.

## Resultado de ejecución

`--apply` no fue ejecutado sobre datos comerciales en esta fase.

## Validación

37 pruebas específicas correctas. `python manage.py check` correcto y `python manage.py makemigrations --check --dry-run` sin cambios. Las pruebas de aplicación usan bases temporales de test. No se aplicaron migraciones ni se ejecutó `--apply` sobre la SQLite comercial.

## Riesgos pendientes

- Debe realizarse respaldo y autorización independiente antes de una futura aplicación.
- Las identificaciones con distinto formato se validan en Python; la base no tiene un índice funcional de identidad normalizada.
- Los duplicados y coincidencias requieren revisión manual posterior.

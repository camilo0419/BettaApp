# BettaApp — Importación controlada de clientes Alegra (Fase 7.3)

## Estado: APLICACIÓN EJECUTADA

El comando reconstruye el plan con datos actuales. El comportamiento por defecto es `--dry-run`; no se crean clientes ni mapeos en ese modo. Las consultas externas son exclusivamente GET.

La ejecución efectiva fue autorizada únicamente para la SQLite local. No se accedió a producción, cPanel ni se escribieron datos en Alegra.

## Respaldo previo

- Ruta: `C:\Users\camil\OneDrive\Escritorio\Python Scripts\betta_diseno_mvp_backups\db.sqlite3.pre_alegra_clients_20261008_081528.bak`.
- Método: API `sqlite3.Connection.backup`, no copia directa de archivo en uso.
- Tamaño: 3.084.288 bytes.
- `PRAGMA integrity_check`: `ok`.
- Conteo previo de clientes: 2.
- Conteo previo de mapeos externos: 0.
- Puntos de venta previos: 0.

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

- Creados: 154.
- Omitidos: 0.
- Errores: 2; ambos fueron rechazados por la restricción local de correo único. No se expusieron los valores.

No se importaron los 8 contactos `REVIEW_DUPLICATE` ni los 3 `SKIP_INVALID`. Los 2 contactos con error de validación permanecen pendientes para revisión; no se modificaron clientes existentes ni se eliminaron datos.

## Verificación posterior

- Integridad SQLite actual: `ok`.
- Clientes actuales: 156.
- Clientes creados: 154.
- Clientes locales anteriores conservados: 2 de 2.
- Mapeos activos de contactos: 154.
- Clientes nuevos sin mapeo activo: 0.
- Grupos duplicados por ID externo: 0.
- Grupos duplicados por objeto local: 0.
- Puntos de venta actuales: 0; no se modificaron.
- Registro de auditoría de la importación: 1, con resultado parcial.

Dry-run posterior, ejecutado sin `--apply`:

- 167 contactos, 6 páginas HTTP 200, cobertura completa.
- `NO_ACTION`: 154.
- `CREATE_LOCAL`: 2 pendientes por validación de correo.
- `REVIEW_DUPLICATE`: 8.
- `SKIP_INVALID`: 3.
- Errores de API: 0.

Esto demuestra que los 154 vínculos creados son idempotentes. No se ejecutó un segundo `--apply`.

## Validación

37 pruebas específicas correctas antes de la ejecución. `python manage.py check` correcto y `python manage.py makemigrations --check --dry-run` sin cambios. Las pruebas de aplicación usan bases temporales de test. No se aplicaron migraciones.

## Riesgos pendientes

- Los 2 contactos rechazados por correo único requieren decisión manual; no deben resolverse sobrescribiendo clientes existentes.
- Las identificaciones con distinto formato se validan en Python; la base no tiene un índice funcional de identidad normalizada.
- Los duplicados y coincidencias requieren revisión manual posterior.

# BettaApp — Fase 7.4: ejecución local de migración y recuperación

## Entorno y respaldo

La ejecución se realizó desde el repositorio local de BettaApp:

`C:\Users\camil\OneDrive\Escritorio\Python Scripts\betta_diseno_mvp`

La base activa fue verificada como SQLite local `db.sqlite3`, coincidente con `BASE_DIR`. No se accedió a producción, cPanel ni a una base remota.

Respaldo creado antes de cualquier modificación:

`C:\Users\camil\OneDrive\Escritorio\Python Scripts\betta_diseno_mvp_backups\db.sqlite3.pre_migration_0029_recovery_20261008_085657.bak`

- Método: `sqlite3.Connection.backup`.
- Tamaño: 3.141.632 bytes.
- Integridad del respaldo: `PRAGMA integrity_check = ok`.
- Clientes antes: 156.
- Mapeos totales antes: 154; mapeos activos: 154.
- Puntos de venta antes: 0.

## Migración 0029

Se inspeccionó `tienda/migrations/0029_remove_cliente_cliente_email_unico_si_existe.py`. Contiene únicamente:

`RemoveConstraint(model_name='cliente', name='cliente_email_unico_si_existe')`.

La migración se aplicó correctamente:

```text
python manage.py migrate tienda 0029
Applying tienda.0029_remove_cliente_cliente_email_unico_si_existe... OK
```

No modifica usuarios, identificación fiscal, `ExternalObjectMap` ni relaciones comerciales.

## Recuperación dirigida

Los dos IDs pendientes se identificaron reconstruyendo el plan actual mediante GET de `/contacts`, cruzándolo con los mapeos activos y seleccionando únicamente los dos registros que continuaban como `CREATE_LOCAL`. No se expusieron los IDs ni datos personales en este informe.

Se ejecutó el comando dirigido con confirmación local separada:

```text
python manage.py alegra_recuperar_clientes --external-id ID_1 ID_2 --apply --confirm "RECUPERAR CLIENTES LOCALMENTE"
```

La operación consultó únicamente Alegra mediante GET y creó localmente 2 clientes con 2 `ExternalObjectMap` activos. Cada alta y su mapeo se ejecutaron dentro de una transacción atómica. No se fusionaron contactos ni se sobrescribieron clientes existentes.

## Verificación posterior

- Clientes actuales: 158.
- Clientes creados en esta recuperación: 2.
- Clientes anteriores conservados: 156 de 156.
- Mapeos activos de contactos: 156.
- Clientes nuevos sin mapeo activo: 0.
- Grupos duplicados por ID externo: 0.
- Grupos duplicados por objeto local: 0.
- Puntos de venta actuales: 0; permanecieron intactos.
- Grupos de correo repetido: 2; ahora son válidos como atributo repetible.
- Integridad SQLite actual: `PRAGMA integrity_check = ok`.
- Escrituras hacia Alegra: 0.

Los dos contactos recuperados superaron las validaciones y ya tienen correspondencia estable. Los 8 contactos con identificación duplicada y los 3 inválidos no fueron incluidos en esta recuperación.

## Validaciones técnicas

```text
python manage.py check
System check identified no issues (0 silenced).

python manage.py makemigrations --check --dry-run
No changes detected

Pruebas relevantes
Ran 44 tests in 3.146s
OK
```

Las pruebas usaron bases temporales y cubrieron identidad por ID, correo repetido, recuperación dirigida, mapeos, idempotencia, rollback transaccional, respuesta incompleta y ausencia de escrituras externas.

## Estado final y riesgos

La migración 0029 está aplicada localmente y la recuperación de los dos contactos autorizados está completada. La creación bidireccional hacia Alegra continúa deshabilitada: solo existen planes y adaptadores simulados; no se habilitaron POST, PUT, PATCH ni DELETE.

Riesgos pendientes:

1. Los 8 duplicados fiscales requieren revisión manual.
2. Los 3 registros inválidos requieren completar o corregir datos con evidencia.
3. La cola persistente de sincronización bidireccional aún no existe.
4. La base local modificada debe respaldarse nuevamente antes de cualquier operación futura de mayor alcance.

## Veredicto

**Ejecución local satisfactoria con recuperación completa de los dos contactos autorizados.** La integridad referencial y de SQLite fue verificada; no hubo cambios externos ni acceso a producción.

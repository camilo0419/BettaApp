# BettaApp — Fase 7.8: migración 0030 y preparación de prueba real

Fecha: 2026-10-08  
Entorno: SQLite local del repositorio. No se accedió a producción, cPanel ni a bases remotas. No se enviaron escrituras HTTP a Alegra.

## Resultado ejecutivo

La migración 0030 se aplicó correctamente sobre la SQLite local después de crear y verificar un respaldo consistente. `AlegraWriteOperation` ya puede persistir operaciones de forma durable, pero no existen operaciones comerciales persistidas y el interruptor de escritura externa continúa deshabilitado.

La prueba real de Alegra **no se ejecutó**. El flujo quedó preparado mediante una herramienta de un solo cliente con dry-run por defecto, consulta GET previa, confirmación exacta y bloqueo de candidatos ambiguos.

## Respaldo

Ruta:

```text
C:\Users\camil\bettaapp_local_backups\db.sqlite3.fase7_8-20261008-092327.bak
```

Validaciones del respaldo:

- Creado mediante `sqlite3.Connection.backup`, no mediante copia insegura del archivo activo.
- Tamaño: 3.141.632 bytes.
- `PRAGMA integrity_check`: `ok`.
- Clientes antes: 158.
- Mapeos externos activos antes: 156.
- Puntos de venta antes: 0.
- Última migración de Tienda antes: 0029.

El respaldo está fuera del repositorio y no contiene credenciales añadidas por esta fase.

## Migración 0030

Archivo: `tienda/migrations/0030_alegrawriteoperation.py`.

La migración crea exclusivamente la tabla `tienda_alegrawriteoperation`, con:

- FK protegida a `Cliente` y `ExternalSystem`.
- unicidad de `idempotency_key`;
- índices por cliente/estado y recurso/ID externo;
- restricción de operación (`create` o `update`);
- estados para pendiente, enviada, sincronizada, fallida, conflicto y conciliación requerida.

Aplicación ejecutada:

```text
python manage.py migrate tienda 0030 --no-color
```

Resultado: `Applying tienda.0030_alegrawriteoperation... OK`.

## Conteos posteriores

| Métrica | Antes | Después |
|---|---:|---:|
| Clientes | 158 | 158 |
| Mapeos externos activos | 156 | 156 |
| Puntos de venta | 0 | 0 |
| Operaciones de escritura | 0 | 0 |
| Integridad SQLite | ok | ok |
| Migración 0030 | pendiente | aplicada |

No se modificaron clientes, puntos de venta, ventas, cartera, cotizaciones ni relaciones comerciales.

## Persistencia operativa

`AlegraWriteOperation` conserva cliente, tipo de operación, estado, clave idempotente, intentos, fechas, error depurado, ID externo confirmado y razón de conciliación. No conserva tokens, encabezados Authorization ni payloads completos.

La persistencia se probó en bases temporales de Django mediante:

- creación idempotente de operación;
- transición a sincronizada con mapeo;
- estado `NEEDS_RECONCILIATION` tras resultado incierto;
- bloqueo de repetición automática;
- actualización confirmada mediante GET simulado.

## Cliente de prueba

No se insertó un cliente ficticio en la SQLite comercial y no se utilizaron los dos clientes locales pendientes. Los datos de prueba se crearon únicamente en la base temporal de tests con valores sintéticos.

La prevalidación simulada cubrió:

```text
cliente temporal → validación → payload allowlistado → ausencia de mapeo
→ consulta de candidatos simulada → operación pendiente
→ respuesta simulada → mapeo atómico confirmado
```

La identificación fiscal exigida por el contrato local se usó únicamente en fixtures sintéticos; no se inventaron documentos para una cuenta real.

## Comando seguro de un único POST

Se creó:

```text
python manage.py alegra_escritura_cliente --client-id ID
```

Por defecto ejecuta dry-run y solo muestra campos del payload, no valores personales ni secretos. Antes de cualquier preparación consulta candidatos por GET.

Una futura ejecución excepcional requeriría:

```text
python manage.py alegra_escritura_cliente \
  --client-id ID \
  --execute \
  --confirm "AUTORIZAR UN SOLO POST ALEGRA"
```

El comando bloquea la operación si:

- falta la confirmación exacta;
- existen candidatos externos;
- el cliente ya tiene un mapeo activo;
- la variable explícita de escritura no está habilitada;
- el resultado anterior requiere conciliación.

La variable de entorno por sí sola no autoriza la operación: también se exige autorización en memoria acotada al cliente, operación y entorno. No se modificó `.env` y no se habilitó la variable.

## Seguridad y límites

- `alegra_sync_clientes` continúa siendo GET-only y no activa POST/PUT.
- Importadores, dry-runs y pruebas no reciben autorización de escritura.
- No hay sincronización saliente masiva.
- Un timeout o respuesta sin ID bloquea el reintento ciego.
- Una contradicción de identidad deja la operación en conciliación requerida.
- Las actualizaciones exigen mapeo activo, comparación contra baseline y confirmación posterior mediante GET.

## Pruebas

Ejecutados:

```text
python manage.py check --no-color
python manage.py makemigrations --check --dry-run --no-color
python manage.py test tienda.test_fase7_7 tienda.test_fase7_6 tienda.test_fase7_4 tienda.test_alegra_client tienda.test_alegra_bidirectional_clients --no-color
```

Resultado: **34 pruebas correctas, 0 fallos, 0 errores**.

También se verificó:

- tabla y claves foráneas de `AlegraWriteOperation`;
- índices y unicidad de la clave idempotente;
- migración 0030 registrada;
- integridad SQLite posterior;
- ausencia de operaciones persistidas;
- dry-run del comando sin crear operaciones.

## Procedimiento futuro para la primera escritura real

1. Crear respaldo nuevo y verificar que la base sea SQLite local.
2. Seleccionar un cliente sintético o autorizado que no tenga mapeo.
3. Ejecutar el comando sin `--execute` y revisar únicamente agregados.
4. Confirmar por GET que no existan candidatos.
5. Habilitar temporalmente la configuración externa en un proceso controlado, nunca de forma permanente.
6. Ejecutar exactamente un `--execute` con la confirmación literal.
7. Verificar que Alegra retorne un ID y consultar ese ID mediante GET.
8. Comprobar el `ExternalObjectMap` y el estado `SYNCED` de `AlegraWriteOperation`.
9. Deshabilitar inmediatamente la escritura externa.
10. Ejecutar el diagnóstico GET-only y confirmar que no aparece una segunda creación pendiente.
11. Ante timeout, respuesta sin ID o contradicción, detenerse y resolver `NEEDS_RECONCILIATION`; nunca repetir automáticamente el POST.

Este procedimiento requiere autorización posterior y no fue ejecutado en Fase 7.8.

## Riesgos y pendientes

- Alegra no documenta una clave idempotente HTTP para `POST /contacts`; la seguridad contra duplicados depende de la operación local y de conciliación dirigida.
- No se ha probado el contrato de escritura contra una respuesta real; solo con mocks.
- La migración 0030 ya está aplicada localmente, pero todavía no existe un proceso de despliegue autorizado.
- La escritura externa sigue bloqueada por diseño.

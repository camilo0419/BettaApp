# Alegra — Fase 1.1: conciliación inteligente de productos

## Alcance

Esta fase agrega conciliación local sobre el staging de productos Alegra. BettaApp continúa siendo el sistema maestro. No se escriben datos en Alegra, no se sincronizan precios comerciales y no se implementan inventarios, clientes ni facturación.

## Clasificaciones

| Clasificación | Regla | Acción automática |
| --- | --- | --- |
| Vinculado | Existe un `ExternalObjectMap` activo hacia un `Producto` válido. | Solo informar. |
| Coincidencia probable | Un único producto local coincide por nombre normalizado. | Mostrar candidato; no vincular. |
| Nuevo | No existe candidato local. | Puede pasar a vista previa de importación. |
| Conflicto | Hay múltiples candidatos por nombre. | Bloquear importación automática. |
| Incompleto | Falta nombre o el tipo externo es variante/kit no soportado. | Revisión manual. |
| Ignorado | Decisión administrativa persistida. | No reprocesar automáticamente. |

Las referencias externas quedan almacenadas en staging y los mapeos se identifican por sistema, tipo de recurso e ID externo. El nombre solo es una señal secundaria y nunca confirma una vinculación.

## Operaciones masivas

El catálogo permite seleccionar únicamente los registros visibles de la página actual. La interfaz muestra el contador y envía IDs repetidos en un POST protegido con CSRF. Las acciones son:

- Clasificar: recalcula las filas seleccionadas y respeta decisiones bloqueadas.
- Importar: abre una vista previa con seleccionados, elegibles, bloqueados, motivos, categoría y tipo de cálculo; al confirmar crea productos locales `activo=False`, con precios en cero y revisión pendiente.
- Ignorar: usa la misma vista previa y marca los elegibles como ignorados localmente.

La confirmación vuelve a consultar la base de datos y revalida elegibilidad. Cada fila se procesa con transacción, se registran resultados en `SyncAuditLog` y se muestran resultados parciales. No se crean variantes, kits o inventario.

## Permisos y seguridad

El acceso conserva `staff` y `view_alegraitemstaging`. Las operaciones que cambian datos locales exigen `change_alegraitemstaging`, POST y CSRF. Las credenciales siguen únicamente en variables de entorno del cliente de solo lectura; ningún token o encabezado se persiste.

## Migración

`tienda/migrations/0020_alegra_reconciliation.py` agrega `classification`, `classification_reason` y `classification_locked` a `AlegraItemStaging`. No altera ni elimina columnas de Fase 1. Debe aplicarse solamente a la base local después de verificar el respaldo SQLite.

En este entorno la aplicación de la migración quedó pendiente porque el proyecto importa `pymysql` al iniciar y el paquete declarado no está instalado; el intento de instalarlo no pudo resolver el índice de paquetes por falta de red. No se utilizó un stub para ocultar esa dependencia.

## Pruebas

Se agregó `tienda/test_alegra_reconciliation.py` para reglas de mapeo externo, coincidencias, conflictos, incompletos, decisiones manuales, importación inactiva/idempotente, vista previa sin efectos, POST/CSRF y permisos. También se conservaron las pruebas de Fase 1 y los mocks HTTP del cliente existente.

La compilación estática de los archivos Python y `git diff --check` pasan. `manage.py check`, `makemigrations --check` y la suite Django requieren resolver primero `pymysql`; no se ejecutaron con dependencias falsas.

## Limitaciones conocidas

- `Producto` no tiene referencia comercial propia; por eso la conciliación usa mapeo externo y nombre normalizado.
- No existe selección persistente entre páginas; “seleccionar visibles” nunca significa todo el catálogo.
- Las categorías de Alegra no se crean localmente de forma automática; el administrador debe escoger una categoría Betta en la confirmación.
- Las operaciones futuras de clientes, documentos, cartera y webhooks quedan fuera de esta fase.

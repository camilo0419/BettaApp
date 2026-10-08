# BettaApp — Preimportación de clientes Alegra (Fase 7.2)

## 1. Entorno

- Ejecución local con SQLite; no se conectó a producción ni cPanel.
- Método externo permitido: `GET` únicamente.
- Persistencia comercial: no; staging, clientes y mapeos no fueron modificados.
- Límites: 300 contactos, 15 páginas, pausa 0.2s, 3 reintentos 429.

## 2. Consulta y cobertura

- Endpoint: `GET /contacts` con `type=client`, `mode=advanced`, paginación `start/limit` y `metadata=true`.
- Contactos recuperados y deduplicados: 167.
- Páginas examinadas: 6.
- HTTP observados: [200, 200, 200, 200, 200, 200].
- Reintentos por HTTP 429: 0.
- Motivo de terminación: `end_of_pagination`.
- Cobertura: 100% (total declarado: 167).
- Porcentaje del catálogo: 100% (total declarado: 167).
- Duplicados de ID externo observados: 0.
- Errores: 0.

## 3. Conciliación simulada

- Clientes locales examinados: 2.
- Ya vinculados (`NO_ACTION`): 0.
- Nuevos propuestos (`CREATE_LOCAL`): 156.
- Coincidencias pendientes (`LINK_EXISTING`): 0.
- Identificaciones duplicadas (`REVIEW_DUPLICATE`): 8.
- Conflictos (`REVIEW_CONFLICT`): 0.
- Inválidos/proveedor puro (`SKIP_INVALID`): 3.

No se muestran nombres, identificaciones, correos, teléfonos ni IDs externos completos. Las acciones son propuestas en memoria y no se ejecutaron.

## 4. Riesgos estructurales

- `Cliente` puede representar los campos requeridos por el contacto normalizado; el ID externo continúa gestionándose mediante `ExternalObjectMap`.
- No se crean puntos de venta a partir de direcciones o contactos Alegra.
- Los contactos proveedor puro se excluyen; los contactos cliente/proveedor se conservan solo en condición de cliente para la simulación.
- Una consulta limitada o con error no se presenta como catálogo completo.
- No se fusionan contactos con identificación repetida.

## 5. Pruebas y recomendación

Pruebas específicas: 29 correctas, cubriendo paginación, límites, final real, HTTP 429, páginas repetidas, duplicados, mapeos, coincidencias, proveedores, inválidos y ausencia de escrituras.
- Comando reproducible de diagnóstico: `python manage.py alegra_preimport_clientes --limit 100 --max-pages 4 --pause 0 --max-retries 2 --timeout 15`.
- Validaciones técnicas: `python manage.py check` correcto; `python manage.py makemigrations --check --dry-run` sin cambios.

**Veredicto: DIAGNÓSTICO COMPLETADO.** Ejecutar una importación efectiva solo después de revisar manualmente conflictos, duplicados y cobertura completa; esta fase no habilita dicha operación.

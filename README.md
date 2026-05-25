# Betta Diseño - MVP Django

MVP funcional para una tienda/cotizador de productos gráficos personalizados.

Inspiración funcional: catálogo de productos configurables antes de enviar solicitud o carrito. La versión Betta queda orientada a revisión comercial y producción real: el cliente configura, adjunta archivo y el equipo administra cada requerimiento desde un panel privado.

## Incluye

### Tienda pública

- Home profesional con marca Betta Diseño.
- Logo incorporado en `static/tienda/img/marca/`.
- Paleta aplicada:
  - `#4abab8`
  - `#034860`
  - `#00a28b`
  - blanco
- Cinco productos iniciales:
  1. Vinilo adhesivo personalizado
  2. Lona / banner publicitario
  3. Pendón tipo roll up
  4. Acrílico / metacrilato personalizado
  5. Letras corpóreas
- Página de personalización por producto.
- Carga de archivo del cliente.
- Cálculo de precio estimado.
- Registro de solicitud/cotización en base de datos.
- Botón para continuar por WhatsApp.

### Panel administrativo propio

Rutas principales:

```text
/panel/login/
/panel/
/panel/solicitudes/
/panel/solicitudes/<id>/
/panel/solicitudes/<id>/editar/
```

Funciones:

- Login con usuario de Django.
- Dashboard con métricas.
- Listado de solicitudes.
- Filtro por estado y buscador.
- Detalle de cada solicitud.
- Cambio rápido de estado.
- Edición de datos, precio, estado, observaciones y notas internas.
- Acceso al archivo adjunto.

Estados disponibles:

```text
Cotización recibida
En revisión de diseño
Pendiente de aprobación
En producción
Lista para entrega
Entregada
Cancelada
```

## Instalación local en Windows / PowerShell

```powershell
cd betta_diseno_mvp
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_products
python manage.py createsuperuser
python manage.py runserver
```

Abrir tienda:

```text
http://127.0.0.1:8000/
```

Abrir panel:

```text
http://127.0.0.1:8000/panel/login/
```

Django admin técnico:

```text
http://127.0.0.1:8000/admin/
```

## Cambiar número de WhatsApp

En `tienda/views.py`, cambia:

```python
WHATSAPP_EMPRESA = '573005551111'
```

por el número real de Betta Diseño en formato internacional, sin `+`.

## Formato para nuevos productos

El archivo para que el cliente diligencie nuevos productos está en:

```text
docs/formato_cliente_nuevo_producto.md
```

Ese documento sirve para convertir rápidamente la información comercial del producto en datos del sistema: materiales, acabados, extras, fórmula de cálculo y reglas especiales.

## Notas técnicas

- Base de datos local: SQLite.
- Framework: Django 5.2.6.
- Archivos subidos: carpeta `media/cotizaciones/`.
- Imágenes públicas: `tienda/static/tienda/img/`.
- Panel propio: vistas protegidas con `login_required`.
- Django admin sigue disponible para administración técnica de productos y opciones.

## Siguiente fase sugerida

- Crear carrito real.
- Enviar correos automáticos.
- Integrar WhatsApp Business API.
- Historial de cambios por solicitud.
- Asignar responsables internos.
- Subida de imágenes reales de trabajos realizados.
- Publicar en Render con PostgreSQL.

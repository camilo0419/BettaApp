# Betta Diseño - Configurador dinámico Django

Aplicación Django para vender/cotizar productos personalizados de diseño, impresión y señalización.

## Funcionalidades

- Home con productos activos.
- Personalizador dinámico por producto.
- Productos configurables desde panel propio, no desde código.
- Campos configurables por producto.
- Opciones/variantes por campo con precio fijo, por m², por unidad o porcentaje.
- Galería de imágenes por producto.
- Solicitudes con respuestas dinámicas y archivos adjuntos.
- Dashboard para staff.
- Gestión de estados y precio final.
- Admin Django renombrado a **Admin Productos**.

## Rutas principales

- Tienda: `/`
- Producto: `/productos/<slug>/`
- Panel staff: `/panel/login/`
- Productos: `/panel/productos/`
- Solicitudes: `/panel/solicitudes/`
- Admin técnico Django: `/admin/`

## Static files y media files

- `STATIC_ROOT` apunta a `staticfiles/` y se actualiza con `python manage.py collectstatic --noinput`.
- `MEDIA_ROOT` apunta por defecto a `media/` o a la ruta definida en `MEDIA_ROOT`.
- `MEDIA_URL` apunta por defecto a `/media/`.
- WhiteNoise se usa para static files, no para media files subidos por usuarios.

Para Conexcol/cPanel, configura el hosting para servir publicamente `/media/` desde `MEDIA_ROOT`. Ver instrucciones detalladas en `docs/media_conexcol_cpanel.md`.

## Instalación local

```bash
cd betta_diseno_configurable
python -m venv venv
source venv/bin/activate  # Windows: .\venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_products
python manage.py createsuperuser
python manage.py runserver
```

## PythonAnywhere

```bash
cd ~
git clone https://github.com/camilo0419/BettaApp.git
cd BettaApp
python3.12 -m venv ~/.virtualenvs/bettaapp-venv
source ~/.virtualenvs/bettaapp-venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python manage.py check
python manage.py migrate
python manage.py seed_products
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

Configurar en **Web**:

- Source code: `/home/camilo0419/BettaApp`
- Working directory: `/home/camilo0419/BettaApp`
- Virtualenv: `/home/camilo0419/.virtualenvs/bettaapp-venv`
- Static: `/static/` -> `/home/camilo0419/BettaApp/staticfiles`
- Media: `/media/` -> `/home/camilo0419/BettaApp/media`

WSGI:

```python
import os
import sys

path = "/home/camilo0419/BettaApp"
if path not in sys.path:
    sys.path.insert(0, path)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```

## Cambiar número de WhatsApp

Editar `tienda/views.py`:

```python
WHATSAPP_EMPRESA = "573005551111"
```

Después hacer **Reload** en PythonAnywhere.

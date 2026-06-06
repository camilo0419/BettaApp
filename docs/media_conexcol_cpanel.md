# Media files en Conexcol/cPanel

Esta aplicacion separa archivos estaticos y media files:

- Static files: CSS, JS, logos, favicon y assets versionados. Se publican con `collectstatic` en `STATIC_ROOT`.
- Media files: imagenes y archivos subidos por usuarios o administradores. Se guardan en `MEDIA_ROOT` y se exponen con `MEDIA_URL`.

## Variables recomendadas

```env
MEDIA_URL=/media/
MEDIA_ROOT=/ruta/absoluta/a/la/carpeta/media
```

Si `MEDIA_ROOT` no se define, Django usa `BASE_DIR / "media"`. En local, si `DJANGO_DEBUG=True`, esa carpeta se crea automaticamente.

## cPanel/Conexcol

En produccion, Django no sirve media files directamente. WhiteNoise queda reservado para static files, no para archivos subidos por usuarios.

Configura en cPanel una de estas opciones segun lo permita el hosting:

1. Mapear publicamente `/media/` hacia la carpeta indicada por `MEDIA_ROOT`.
2. Crear un symlink desde una carpeta publica, por ejemplo `public_html/media`, hacia `MEDIA_ROOT`.
3. Configurar un alias de servidor para que `https://dominio.com/media/archivo.jpg` lea desde `MEDIA_ROOT`.

Verifica tambien:

- permisos de lectura para el servidor web sobre `MEDIA_ROOT`;
- permisos de escritura para el usuario que ejecuta Passenger/WSGI;
- que `STATIC_ROOT`, `MEDIA_ROOT` y las carpetas `static/` del proyecto no apunten a la misma ruta;
- que una URL real como `https://dominio.com/media/productos/imagen.jpg` abra sin pasar por `collectstatic`.

No borres ni muevas archivos existentes de `media/` sin respaldo. Si hay rutas guardadas en base de datos cuyo archivo ya no existe, la aplicacion mostrara un fallback en vez de un error 500, pero conviene auditar esos archivos faltantes.

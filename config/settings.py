from pathlib import Path
import os

from django.core.exceptions import ImproperlyConfigured

try:
    from dotenv import load_dotenv
except (ImportError, PermissionError):
    def load_dotenv(path):
        env_path = Path(path)
        if not env_path.exists():
            return False
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        return True

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    value = os.environ.get(name)
    if value in [None, ""]:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} debe ser un entero.") from exc


def env_list(name, default=None):
    value = os.environ.get(name)
    if value in [None, ""]:
        return default or []
    return [item.strip() for item in value.split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-local-development-only-change-me"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY es obligatorio cuando DJANGO_DEBUG=False.")

if not DEBUG and SECRET_KEY in {
    "dev-key-cambiar-en-produccion",
    "django-insecure-local-development-only-change-me",
    "change-me",
    "change-me-in-production",
}:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY debe ser un valor real y seguro en producción.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["127.0.0.1", "localhost"])
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "tienda",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "tienda.context_processors.betta_global_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": os.environ.get("DB_ENGINE", "django.db.backends.sqlite3"),
        "NAME": os.environ.get("DB_NAME", BASE_DIR / "db.sqlite3"),
        "USER": os.environ.get("DB_USER", ""),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", ""),
        "PORT": os.environ.get("DB_PORT", ""),
        "OPTIONS": {
            "charset": "utf8mb4",
        } if os.environ.get("DB_ENGINE") == "django.db.backends.mysql" else {},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es-co"
TIME_ZONE = "America/Bogota"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/panel/login/"
LOGIN_REDIRECT_URL = "/panel/"
LOGOUT_REDIRECT_URL = "/panel/login/"

SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", False)
SESSION_COOKIE_SECURE = env_bool("DJANGO_SESSION_COOKIE_SECURE", False)
CSRF_COOKIE_SECURE = env_bool("DJANGO_CSRF_COOKIE_SECURE", False)
SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", False)

FILE_UPLOAD_MAX_MEMORY_SIZE = env_int("DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE", 10 * 1024 * 1024)
DATA_UPLOAD_MAX_MEMORY_SIZE = env_int("DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE", 15 * 1024 * 1024)
USER_UPLOAD_MAX_SIZE = env_int("DJANGO_USER_UPLOAD_MAX_SIZE", 10 * 1024 * 1024)
PRODUCT_IMAGE_UPLOAD_MAX_SIZE = env_int("DJANGO_PRODUCT_IMAGE_UPLOAD_MAX_SIZE", 5 * 1024 * 1024)

ALLOWED_USER_UPLOAD_EXTENSIONS = env_list(
    "DJANGO_ALLOWED_USER_UPLOAD_EXTENSIONS",
    [".pdf", ".jpg", ".jpeg", ".png", ".webp", ".zip", ".ai"],
)
ALLOWED_IMAGE_UPLOAD_EXTENSIONS = env_list(
    "DJANGO_ALLOWED_IMAGE_UPLOAD_EXTENSIONS",
    [".jpg", ".jpeg", ".png", ".webp"],
)

EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "").strip()
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "").strip()
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com").strip()
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", 15)
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend").strip()
if EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend" and (not EMAIL_HOST_USER or not EMAIL_HOST_PASSWORD):
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

BETTA_EMAIL_FROM_NAME = os.environ.get("BETTA_EMAIL_FROM_NAME", "Betta Diseño").strip()
BETTA_EMAIL_LOGO_URL = os.environ.get("BETTA_EMAIL_LOGO_URL", "").strip()
BETTA_WHATSAPP_NUMBER = os.environ.get("BETTA_WHATSAPP_NUMBER", "").strip()
BETTA_WHATSAPP_MESSAGE = os.environ.get(
    "BETTA_WHATSAPP_MESSAGE",
    "Hola, estoy visitando la pagina de Betta Diseno y necesito asesoria personalizada para un producto o proyecto que no encontre en la tienda.",
).strip()
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "").strip() or EMAIL_HOST_USER or "webmaster@localhost"
SERVER_EMAIL = os.environ.get("SERVER_EMAIL", "").strip() or DEFAULT_FROM_EMAIL
SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8000").strip().rstrip("/")

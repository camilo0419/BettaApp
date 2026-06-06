import logging

from django import template
from django.core.files.storage import FileSystemStorage

register = template.Library()
logger = logging.getLogger(__name__)


@register.filter
def safe_media_url(file_field):
    if not file_field:
        return ""

    name = getattr(file_field, "name", "")
    if not name:
        return ""

    storage = getattr(file_field, "storage", None)
    if isinstance(storage, FileSystemStorage):
        try:
            if not storage.exists(name):
                return ""
        except Exception:
            logger.warning("No se pudo comprobar existencia de media: %s", name, exc_info=True)
            return ""

    try:
        return file_field.url
    except Exception:
        logger.warning("No se pudo construir URL de media: %s", name, exc_info=True)
        return ""

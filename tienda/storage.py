"""Almacenamiento para archivos administrativos que no deben tener URL pública."""

from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.files.utils import validate_file_name
from django.utils._os import safe_join


class PrivateMediaStorage(FileSystemStorage):
    """Guarda archivos nuevos fuera de MEDIA_ROOT y no genera URLs públicas.

    ``path`` conserva lectura de archivos antiguos bajo MEDIA_ROOT para que los
    trabajos ya creados sigan siendo procesables durante la transición.
    """

    @property
    def location(self):
        return Path(settings.PRIVATE_MEDIA_ROOT)

    def _legacy_path(self, name):
        return Path(safe_join(settings.MEDIA_ROOT, name))

    def path(self, name):
        validate_file_name(name, allow_relative_path=True)
        private_path = Path(safe_join(self.location, name))
        if private_path.exists():
            return private_path
        legacy_path = self._legacy_path(name)
        return legacy_path if legacy_path.exists() else private_path

    def url(self, name):
        raise ValueError("Los archivos privados de importación no tienen URL pública.")

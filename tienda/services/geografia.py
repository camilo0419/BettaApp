"""Consultas locales del catálogo DIVIPOLA, sin llamadas externas por request."""

from __future__ import annotations

import unicodedata

from django.core.exceptions import ValidationError
from django.db.models import Q

from tienda.models import ColombiaMunicipio


class SyncLocationValidationError(ValidationError):
    """La ubicación no permite construir un contacto colombiano completo."""


def validate_sync_location(client, *, require_catalog=False):
    """Valida la ubicación que se enviará a Alegra.

    ``require_catalog`` se usa en los formularios de altas nuevas para evitar
    que el navegador envíe una combinación de ciudad/departamento no verificable.
    Los registros históricos pueden conservar texto legado si ambos valores
    existen y no se está intentando cambiar su ubicación.
    """
    city = str(getattr(client, "ciudad", "") or "").strip()
    department = str(getattr(client, "departamento", "") or "").strip()
    if not department:
        raise SyncLocationValidationError("Selecciona un departamento para continuar")
    if not city:
        raise SyncLocationValidationError("Selecciona una ciudad para continuar")
    municipality = getattr(client, "divipola_municipio", None)
    if require_catalog and municipality is None:
        raise SyncLocationValidationError("Selecciona un departamento para continuar")
    if municipality is not None:
        if normalize_location(municipality.nombre) != normalize_location(city):
            raise SyncLocationValidationError("La ciudad no coincide con el departamento seleccionado.")
        if normalize_location(municipality.departamento) != normalize_location(department):
            raise SyncLocationValidationError("La ciudad no pertenece al departamento seleccionado.")


def normalize_location(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in value if not unicodedata.combining(char)).casefold().strip()


def search_municipios(query: str, *, limit: int = 20):
    """Busca por prefijo/código usando el catálogo local y valida en Python tildes."""
    term = normalize_location(query)
    if len(term) < 2:
        return []
    candidates = ColombiaMunicipio.objects.filter(activo=True).order_by("departamento", "nombre", "codigo")
    if term.isdigit():
        candidates = candidates.filter(codigo__startswith=term)
    else:
        # El ORM de SQLite/MariaDB no garantiza búsqueda sin tildes. Reducimos
        # en SQL por un prefijo estable y confirmamos la coincidencia normalizada
        # en Python para conservar el comportamiento entre motores.
        prefix = term[:3]
        candidates = candidates.filter(Q(nombre__istartswith=prefix) | Q(departamento__istartswith=prefix))
    results = []
    for municipio in candidates[: max(limit * 4, limit)]:
        if term.isdigit() or term in normalize_location(municipio.nombre) or term in normalize_location(municipio.departamento):
            results.append(municipio)
        if len(results) >= limit:
            break
    return results


def alegra_location(municipio: ColombiaMunicipio | None) -> dict[str, str]:
    """Adapta DIVIPOLA a los nombres de texto que usa Alegra en contactos."""
    if municipio is None:
        return {}
    return {"city": municipio.nombre, "department": municipio.departamento, "country": "Colombia"}

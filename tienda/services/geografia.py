"""Consultas locales del catálogo DIVIPOLA, sin llamadas externas por request."""

from __future__ import annotations

import unicodedata

from django.db.models import Q

from tienda.models import ColombiaMunicipio


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

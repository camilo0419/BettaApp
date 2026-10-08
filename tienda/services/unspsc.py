"""Búsqueda y recomendaciones locales sobre el catálogo UNSPSC oficial."""

import re
import unicodedata

from django.db.models import Q

from tienda.models import UNSPSCCode


def normalize_text(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in value if not unicodedata.combining(char)).casefold()


def _tokens(value):
    return {token for token in re.findall(r"[a-z0-9]+", normalize_text(value)) if len(token) > 1}


def recommend_unspsc(query, *, limit=5):
    query = str(query or "").strip()
    if len(query) < 2:
        return []
    normalized = normalize_text(query)
    code_query = re.sub(r"\D", "", query)
    terms = _tokens(query)
    filters = Q(description__icontains=query) | Q(code__icontains=code_query or query)
    for term in terms:
        filters |= Q(description__icontains=term)
    candidates = UNSPSCCode.objects.filter(active=True).filter(filters).select_related("parent")[:100]
    ranked = []
    for item in candidates:
        description = normalize_text(item.description)
        score = 0
        if code_query and item.code == code_query:
            score += 1000
        elif code_query and code_query in item.code:
            score += 300
        if normalized and normalized in description:
            score += 300
        matched = sum(1 for term in terms if term in description)
        score += matched * 30
        if description.startswith(normalized):
            score += 50
        ranked.append((score, item.code, item))
    ranked.sort(key=lambda value: (-value[0], value[1]))
    return [item for _, _, item in ranked[:max(1, min(limit, 5))]]


def unspsc_result(item):
    return {
        "id": item.pk,
        "code": item.code,
        "description": item.description,
        "level": item.get_level_display(),
        "parent": item.parent.code if item.parent_id else "",
        "version": item.catalog_version,
    }

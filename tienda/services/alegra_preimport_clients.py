"""Diagnóstico y simulación en memoria de la preimportación de contactos."""

from __future__ import annotations

import hashlib
import time
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from .alegra_client import AlegraError, AlegraHTTPError, extract_rows
from .alegra_contact_import import normalize_identity


ACTION_NO_ACTION = "NO_ACTION"
ACTION_CREATE_LOCAL = "CREATE_LOCAL"
ACTION_LINK_EXISTING = "LINK_EXISTING"
ACTION_REVIEW_DUPLICATE = "REVIEW_DUPLICATE"
ACTION_REVIEW_CONFLICT = "REVIEW_CONFLICT"
ACTION_SKIP_INVALID = "SKIP_INVALID"


def _type_tokens(row: Mapping[str, Any]) -> set[str]:
    value = row.get("type")
    if isinstance(value, dict):
        value = value.get("name") or value.get("type") or value.get("code")
    values = value if isinstance(value, list) else [value]
    return {str(item).strip().casefold() for item in values if item not in (None, "")}


def _is_customer(row: Mapping[str, Any]) -> bool:
    return bool(_type_tokens(row) & {"client", "cliente", "customer", "clienta"})


def _is_provider_only(row: Mapping[str, Any]) -> bool:
    tokens = _type_tokens(row)
    return bool(tokens) and not _is_customer(row) and bool(tokens & {"provider", "proveedor", "supplier"})


def _external_id(row: Mapping[str, Any]) -> str:
    return str(row.get("id") or row.get("external_id") or "").strip()


def _name(row: Mapping[str, Any]) -> str:
    return str(row.get("name") or "").strip()


def _identity(row: Mapping[str, Any]) -> str:
    return normalize_identity(row.get("identification"))


def anonymized_id(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def fetch_customer_contacts(client, *, limit=100, max_pages=10, pause=0, max_retries=2):
    """Consulta contactos con GET, límites, reintentos 429 y cobertura explícita."""
    limit = None if limit is None else min(max(int(limit), 1), 10000)
    max_pages = None if max_pages is None else min(max(int(max_pages), 1), 10000)
    pause = min(max(float(pause), 0), 60)
    max_retries = min(max(int(max_retries), 0), 5)
    page_size = min(limit or 30, 30)
    start = 0
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    observed_id_counts: Counter[str] = Counter()
    seen_page_fingerprints: set[tuple[str, ...]] = set()
    statuses: list[int] = []
    pages = 0
    retries_429 = 0
    stopped_reason = ""
    declared_total = None
    errors: list[str] = []

    while (max_pages is None or pages < max_pages) and (limit is None or len(rows) < limit):
        current = min(page_size, limit - len(rows)) if limit is not None else page_size
        params = {"type": "client", "mode": "advanced", "start": start, "limit": current, "metadata": "true"}
        response = None
        for attempt in range(max_retries + 1):
            try:
                response = client.get("/contacts", params)
                break
            except AlegraHTTPError as exc:
                if exc.status != 429 or attempt >= max_retries:
                    errors.append(str(exc))
                    stopped_reason = "error"
                    break
                retries_429 += 1
                if pause:
                    time.sleep(pause)
            except AlegraError as exc:
                errors.append(str(exc))
                stopped_reason = "error"
                break
        if response is None:
            break
        statuses.append(response.status)
        page_rows = [row for row in extract_rows(response.data) if isinstance(row, dict)]
        page_ids = tuple(_external_id(row) or f"missing:{index}" for index, row in enumerate(page_rows))
        if page_ids in seen_page_fingerprints and page_rows:
            stopped_reason = "repeated_page"
            break
        seen_page_fingerprints.add(page_ids)
        if isinstance(response.data, dict):
            metadata = response.data.get("metadata")
            if isinstance(metadata, dict):
                raw_total = metadata.get("total") or metadata.get("count")
                try:
                    declared_total = int(raw_total) if raw_total is not None else declared_total
                except (TypeError, ValueError):
                    pass
        pages += 1
        for row in page_rows:
            external_id = _external_id(row)
            if external_id:
                observed_id_counts[external_id] += 1
            if external_id and external_id not in seen_ids:
                seen_ids.add(external_id)
                rows.append(row)
        start += len(page_rows)
        if not page_rows:
            stopped_reason = "end_of_pagination"
            break
        if declared_total is not None and start >= declared_total:
            stopped_reason = "end_of_pagination"
            break
        if limit is not None and len(rows) >= limit:
            stopped_reason = "limit"
            break
        if pause:
            time.sleep(pause)
    if not stopped_reason:
        stopped_reason = "max_pages" if max_pages is not None and pages >= max_pages else "limit"
    if errors and stopped_reason != "error":
        stopped_reason = "error"
    coverage = "complete" if stopped_reason == "end_of_pagination" else "limited" if stopped_reason in {"limit", "max_pages"} else "incomplete"
    return {
        "rows": rows,
        "pages": pages,
        "http_statuses": statuses,
        "retries_429": retries_429,
        "duplicate_external_ids": len([item for item, count in observed_id_counts.items() if count > 1]),
        "stopped_reason": stopped_reason,
        "coverage": coverage,
        "declared_total": declared_total,
        "errors": errors,
    }


def build_preimport_plan(rows: Iterable[Mapping[str, Any]], *, local_clients=(), active_maps=(), all_maps=()):
    """Construye acciones propuestas sin guardar staging, clientes ni mapeos."""
    rows = list(rows)
    local_clients = list(local_clients)
    active_maps = list(active_maps)
    all_maps = list(all_maps)
    by_identity: dict[str, list[Any]] = defaultdict(list)
    for client in local_clients:
        identity = normalize_identity(getattr(client, "identificacion", ""))
        if identity:
            by_identity[identity].append(client)
    external_id_counts = Counter(_external_id(row) for row in rows if _external_id(row))
    identity_counts = Counter(_identity(row) for row in rows if _identity(row))
    active_by_external: dict[str, list[Any]] = defaultdict(list)
    active_any_by_external: dict[str, list[Any]] = defaultdict(list)
    all_by_external: dict[str, list[Any]] = defaultdict(list)
    for mapping in active_maps:
        active_by_external[str(mapping.external_id)].append(mapping)
    for mapping in all_maps:
        all_by_external[str(mapping.external_id)].append(mapping)
        if getattr(mapping, "status", "") == "active":
            active_any_by_external[str(mapping.external_id)].append(mapping)
    plans = []
    for row in rows:
        external_id = _external_id(row)
        identity = _identity(row)
        tokens = _type_tokens(row)
        candidate_clients = by_identity.get(identity, []) if identity else []
        reason = ""
        if not external_id or not _name(row) or not identity or not tokens:
            action, reason = ACTION_SKIP_INVALID, "Falta ID externo, nombre, identificación o tipo."
        elif _is_provider_only(row):
            action, reason = ACTION_SKIP_INVALID, "Contacto proveedor sin condición de cliente."
        elif external_id_counts[external_id] > 1:
            action, reason = ACTION_REVIEW_CONFLICT, "El ID externo se repite en la respuesta."
        elif len({getattr(mapping, "object_id", None) for mapping in all_by_external.get(external_id, [])}) > 1:
            action, reason = ACTION_REVIEW_CONFLICT, "Existen mapeos contradictorios para el ID externo."
        elif active_any_by_external.get(external_id) and not active_by_external.get(external_id):
            action, reason = ACTION_REVIEW_CONFLICT, "Existe un mapeo activo que no corresponde a un cliente local válido."
        elif len(active_by_external.get(external_id, [])) == 1:
            action, reason = ACTION_NO_ACTION, "Ya vinculado por ID externo."
        elif identity_counts[identity] > 1:
            action, reason = ACTION_REVIEW_DUPLICATE, "La identificación se repite entre contactos Alegra."
        elif len(candidate_clients) > 1:
            action, reason = ACTION_REVIEW_CONFLICT, "Coincide con varios clientes locales."
        elif len(candidate_clients) == 1:
            action, reason = ACTION_LINK_EXISTING, "Coincidencia inequívoca por identificación; se actualizará y vinculará."
        else:
            action, reason = ACTION_CREATE_LOCAL, "No hay coincidencia local por identificación."
        plans.append({
            "action": action,
            "reason": reason,
            "external_id_hash": anonymized_id(external_id) if external_id else "",
            "candidate_ids": [getattr(item, "pk", None) for item in candidate_clients],
            "customer_and_provider": bool(_is_customer(row) and tokens & {"provider", "proveedor", "supplier"}),
        })
    return {
        "plans": plans,
        "counts": dict(Counter(item["action"] for item in plans)),
        "customers_examined": len(rows),
        "local_clients_examined": len(local_clients),
        "simulated": True,
        "writes": False,
    }


def fetch_contacts_by_id(client, external_ids: Iterable[str]):
    """Recuperación dirigida GET-only; no persiste respuestas ni staging."""
    unique_ids = []
    for value in external_ids:
        external_id = str(value or "").strip()
        if external_id and external_id not in unique_ids:
            unique_ids.append(external_id)
    recovered, errors, incomplete = [], [], []
    for external_id in unique_ids:
        try:
            response = client.get(f"/contacts/{quote(external_id, safe='')}", {"mode": "advanced"})
            rows = extract_rows(response.data)
            row = response.data if isinstance(response.data, dict) and response.data.get("id") else (rows[0] if len(rows) == 1 and isinstance(rows[0], dict) else None)
            if not isinstance(row, dict) or not row.get("id"):
                incomplete.append(external_id)
            else:
                recovered.append(row)
        except AlegraError as exc:
            errors.append({"external_id": external_id, "error": str(exc)})
    return {"requested": unique_ids, "recovered": recovered, "incomplete": incomplete, "errors": errors}

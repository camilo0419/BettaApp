"""Normalización estricta de valores tributarios compartidos con Alegra."""

from __future__ import annotations

import re
from typing import Any, Mapping


IDENTIFICATION_TYPE_ALIASES = {
    "CC": "cc",
    "CEDULA": "cc",
    "CEDULA_DE_CIUDADANIA": "cc",
    "NIT": "nit",
    "CE": "ce",
    "CEDULA_DE_EXTRANJERIA": "ce",
    "PP": "pasaporte",
    "PASAPORTE": "pasaporte",
    "PASSPORT": "pasaporte",
    "OTRO": "otro",
}
KNOWN_IDENTIFICATION_TYPES = frozenset(IDENTIFICATION_TYPE_ALIASES.values())
KNOWN_KINDS = frozenset({"PERSON_ENTITY", "LEGAL_ENTITY", "OTHER_ENTITY"})
KNOWN_REGIMES = frozenset({
    "COMMON_REGIME", "SIMPLIFIED_REGIME", "NATIONAL_CONSUMPTION_TAX",
    "NOT_REPONSIBLE_FOR_CONSUMPTION", "INC_IVA_RESPONSIBLE", "SPECIAL_REGIME",
})


def first_present(mapping: Mapping[str, Any], *keys: str) -> Any:
    """Devuelve el primer valor presente, conservando explícitamente 0."""
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return ""


def _token(value: Any) -> str:
    return re.sub(r"[\s-]+", "_", str(value or "").strip().upper())


def normalize_identification_type(value: Any) -> str | None:
    token = _token(value)
    return IDENTIFICATION_TYPE_ALIASES.get(token)


def normalize_kind_of_person(value: Any) -> str | None:
    token = _token(value)
    return token if token in KNOWN_KINDS else None


def normalize_regime(value: Any) -> str | None:
    token = _token(value)
    return token if token in KNOWN_REGIMES else None


def extract_identification_context(row: Mapping[str, Any]) -> dict[str, Any]:
    identification_object = row.get("identificationObject") if isinstance(row.get("identificationObject"), Mapping) else {}
    raw_type = first_present(row, "identificationType", "identification_type")
    if raw_type in (None, ""):
        raw_type = identification_object.get("type", "")
    raw_number = first_present(row, "identification")
    if raw_number in (None, ""):
        raw_number = identification_object.get("number", "")
    raw_dv = first_present(row, "verificationDigit", "dv")
    if raw_dv in (None, ""):
        raw_dv = identification_object.get("dv", "")
    return {
        "raw_type": str(raw_type or "").strip(),
        "type": normalize_identification_type(raw_type),
        "raw_number": str(raw_number or "").strip(),
        "number": str(raw_number or "").strip(),
        "raw_dv": str(raw_dv).strip() if raw_dv not in (None, "") else "",
        "dv": str(raw_dv).strip() if raw_dv not in (None, "") else "",
        "raw_kind": str(first_present(row, "kindOfPerson") or "").strip(),
        "kind": normalize_kind_of_person(first_present(row, "kindOfPerson")),
        "raw_regime": str(first_present(row, "regime") or "").strip(),
        "regime": normalize_regime(first_present(row, "regime")),
    }

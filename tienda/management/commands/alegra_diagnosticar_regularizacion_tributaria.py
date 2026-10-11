"""Diagnóstico de regularización tributaria histórica, sin escrituras."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError

from tienda.models import Cliente, ExternalObjectMap, ExternalSystem
from tienda.services.alegra_client import AlegraError, AlegraReadOnlyClient
from tienda.services.alegra_normalization import extract_identification_context
from tienda.services.alegra_preimport_clients import fetch_customer_contacts
from tienda.services.database_lock import DatabaseLockUnavailable, advisory_lock


LOCK_NAME = "bettaapp:alegra:historical-tax-diagnostic"
HISTORICAL_SOURCE = "alegra_initial_import"
FIELDS = (
    "regimen_tributario",
    "tipo_identificacion",
    "digito_verificacion",
    "kindOfPerson",
    "identificacion",
)
FIELD_LABELS = {
    "regimen_tributario": "regime",
    "tipo_identificacion": "tipo_identificacion",
    "digito_verificacion": "digito_verificacion",
    "kindOfPerson": "kindOfPerson",
    "identificacion": "identificacion",
}


def _local_values(client):
    return {
        "regimen_tributario": str(client.regimen_tributario or "").strip().upper(),
        "tipo_identificacion": str(client.tipo_identificacion or "").strip().casefold(),
        "digito_verificacion": str(client.digito_verificacion or "").strip(),
        "kindOfPerson": "LEGAL_ENTITY" if client.tipo_cliente == Cliente.TIPO_EMPRESA else "PERSON_ENTITY",
        "identificacion": str(client.identificacion or "").strip().replace(".", ""),
    }


def _remote_values(row):
    identity = extract_identification_context(row)
    return {
        "regimen_tributario": identity.get("regime") or "",
        "tipo_identificacion": identity.get("type") or "",
        "digito_verificacion": identity.get("dv") or "",
        "kindOfPerson": identity.get("kind") or "",
        "identificacion": str(identity.get("number") or "").strip().replace(".", ""),
    }, identity


def classify_field(field, local, remote, *, local_is_default=False):
    """Clasifica un campo sin devolver valores tributarios en el informe."""
    if not remote:
        return "REMOTE_MISSING"
    if local == remote:
        return "ALREADY_MATCHED"
    if local_is_default:
        return "DEFAULT_VALUE_REVIEW"
    if not local:
        return "MISSING_LOCAL_CANDIDATE"
    return "REAL_CONFLICT"


def diagnose_mapping(mapping, client, remote, *, duplicate=False, provenance_unverified=False):
    if duplicate or client is None or remote is None:
        return {
            "record_categories": ["UNVERIFIED_PROVENANCE"],
            "reason": "mapeo_duplicado_o_registro_no_confirmable",
            "fields": {},
            "provenance_verified": False,
        }
    values, identity = _remote_values(remote)
    local = _local_values(client)
    invalid_fields = []
    for key in ("regimen_tributario", "tipo_identificacion", "kindOfPerson"):
        raw_key = {
            "regimen_tributario": "raw_regime",
            "tipo_identificacion": "raw_type",
            "kindOfPerson": "raw_kind",
        }[key]
        if identity.get(raw_key) and not values[key]:
            invalid_fields.append(key)
    fields = {}
    for field in FIELDS:
        fields[FIELD_LABELS[field]] = classify_field(
            field, local[field], values[field],
            local_is_default=(field == "kindOfPerson" and local[field] == "PERSON_ENTITY" and values[field] == "LEGAL_ENTITY"),
        )
    for field in invalid_fields:
        fields[FIELD_LABELS[field]] = "INVALID_REMOTE_VALUE"
    record_categories = ["UNVERIFIED_PROVENANCE"] if provenance_unverified else []
    if invalid_fields:
        record_categories.append("INVALID_REMOTE_VALUE")
    return {
        "record_categories": sorted(set(record_categories)),
        "reason": "valores_remotos_invalidos" if invalid_fields else "diagnostico_por_campo",
        "fields": fields,
        "invalid_fields": [FIELD_LABELS[field] for field in invalid_fields],
        "provenance_verified": False,
    }


class Command(BaseCommand):
    help = "Diagnostica regularización tributaria histórica de Alegra sin modificar datos."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Procesa todos los mapas elegibles.")
        parser.add_argument("--limit", type=int, default=100, help="Máximo de mapas inspeccionados en esta ejecución.")
        parser.add_argument("--after-map-id", type=int, default=0, help="Reanuda después de este ID de mapa.")
        parser.add_argument("--max-pages", type=int, default=10)
        parser.add_argument("--pause", type=float, default=0)
        parser.add_argument("--max-retries", type=int, default=2)
        parser.add_argument("--timeout", type=float, default=None)
        parser.add_argument("--review-output", default="", help="Archivo opcional JSONL con IDs internos para revisión protegida.")

    def handle(self, *args, **options):
        try:
            with advisory_lock(LOCK_NAME):
                return self._run(options)
        except DatabaseLockUnavailable as exc:
            raise CommandError(str(exc)) from exc

    def _run(self, options):
        system = ExternalSystem.objects.filter(code="alegra").first()
        if not system:
            raise CommandError("No existe configuración local de Alegra.")
        fetched = fetch_customer_contacts(
            AlegraReadOnlyClient(timeout=options["timeout"]),
            limit=None if options["all"] else min(max(options["limit"], 1), 1000),
            max_pages=None if options["all"] else min(max(options["max_pages"], 1), 100),
            pause=options["pause"], max_retries=options["max_retries"],
        )
        if fetched["errors"]:
            self.stdout.write(self.style.WARNING(f"Errores GET recuperados: {len(fetched['errors'])}."))
        rows = {str(row.get("id")): row for row in fetched["rows"] if row.get("id")}
        duplicate_ids = {str(value) for value in fetched.get("duplicate_external_ids", [])}
        content_type = ContentType.objects.get_for_model(Cliente)
        queryset = ExternalObjectMap.objects.filter(
            system=system, resource_type="contacts", content_type=content_type,
            status=ExternalObjectMap.STATUS_ACTIVE, pk__gt=max(options["after_map_id"], 0),
        ).order_by("pk")
        if not options["all"]:
            queryset = queryset[:max(options["limit"], 1)]
        mappings = list(queryset)
        clients = Cliente.objects.in_bulk([item.object_id for item in mappings if item.object_id])
        aggregate = Counter()
        field_counts = {field: Counter() for field in FIELD_LABELS.values()}
        review_rows = []
        for mapping in mappings:
            metadata = mapping.metadata if isinstance(mapping.metadata, dict) else {}
            client = clients.get(mapping.object_id)
            remote = rows.get(str(mapping.external_id))
            duplicate = (
                ExternalObjectMap.objects.filter(system=system, resource_type="contacts", content_type=content_type,
                                                  external_id=mapping.external_id, status=ExternalObjectMap.STATUS_ACTIVE).count() != 1
                or ExternalObjectMap.objects.filter(system=system, resource_type="contacts", content_type=content_type,
                                                    object_id=mapping.object_id, status=ExternalObjectMap.STATUS_ACTIVE).count() != 1
                or str(mapping.external_id) in duplicate_ids
            )
            provenance_unverified = bool(client and mapping.created_at and client.fecha_actualizacion and client.fecha_actualizacion > mapping.created_at)
            if metadata.get("source") != HISTORICAL_SOURCE:
                provenance_unverified = True
            result = diagnose_mapping(mapping, client, remote, duplicate=duplicate, provenance_unverified=provenance_unverified)
            for category in result["record_categories"]:
                aggregate[category] += 1
            if not client or not remote:
                aggregate["REMOTE_MISSING" if not remote else "UNVERIFIED_PROVENANCE"] += 1
            for field, category in result["fields"].items():
                field_counts[field][category] += 1
            if options["review_output"]:
                review_rows.append({
                    "map_id": mapping.pk,
                    "local_id": mapping.object_id,
                    "external_id": str(mapping.external_id),
                    "record_categories": result["record_categories"],
                    "fields": result["fields"],
                    "invalid_fields": result.get("invalid_fields", []),
                })
        if options["review_output"]:
            path = Path(options["review_output"]).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as handle:
                for row in review_rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        self.stdout.write(
            f"Modo=DRY-RUN; mapas={len(mappings)}; paginas={fetched['pages']}; "
            f"cobertura={fetched['coverage']}; errores_get={len(fetched['errors'])}; "
            "escrituras_db=0; escrituras_alegra=0."
        )
        self.stdout.write("Categorias de registro: " + ", ".join(f"{key}={value}" for key, value in sorted(aggregate.items())))
        self.stdout.write("Clasificación por campo:")
        for field in FIELD_LABELS.values():
            values = ", ".join(f"{key}={value}" for key, value in sorted(field_counts[field].items())) or "sin_datos=0"
            self.stdout.write(f"- {field}: {values}")
        if options["review_output"]:
            self.stdout.write(f"Archivo protegido de revisión: {options['review_output']}")

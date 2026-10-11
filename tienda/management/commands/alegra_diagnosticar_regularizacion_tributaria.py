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
from tienda.services.alegra_contact_import import normalize_identity
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


def _duplicate_ids_from_rows(rows):
    counts = Counter(str(row.get("id")) for row in rows if row.get("id"))
    return {external_id for external_id, count in counts.items() if count > 1}


def _duplicate_count(value):
    """Normaliza el contador contractual sin convertirlo en IDs ficticios."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(value, 0)
    return 0


def _value_state(value):
    return "present" if str(value or "").strip() else "empty"


def _plan_field(field, local, remote, *, blocked_reason="", provenance_unverified=False, invalid=False):
    label = FIELD_LABELS[field]
    local_state = _value_state(local)
    remote_state = _value_state(remote)
    base = {
        "local_state": local_state,
        "remote_state": remote_state,
    }
    if blocked_reason:
        return {**base, "classification": "BLOCKED", "action": "BLOCK", "reason": blocked_reason}
    if invalid:
        return {**base, "classification": "INVALID_REMOTE_VALUE", "action": "REVIEW",
                "reason": "el valor remoto no es válido para BettaApp"}
    if remote_state == "empty":
        return {**base, "classification": "REMOTE_MISSING", "action": "NO_ACTION",
                "reason": "Alegra no proporcionó un valor confirmable"}
    if local == remote:
        return {**base, "classification": "ALREADY_MATCHED", "action": "NO_ACTION",
                "reason": "los valores coinciden después de normalizar"}
    if provenance_unverified:
        return {**base, "classification": "UNVERIFIED_PROVENANCE", "action": "REVIEW",
                "reason": "no se puede confirmar que el valor local sea heredado"}
    if field == "kindOfPerson" and local == "PERSON_ENTITY" and remote == "LEGAL_ENTITY":
        return {**base, "classification": "DEFAULT_VALUE_REVIEW", "action": "REVIEW",
                "reason": "el valor local persona puede ser un valor predeterminado"}
    if local_state == "empty":
        return {**base, "classification": "SAFE_FILL_CANDIDATE", "action": "FILL_LOCAL",
                "reason": "campo local vacío y valor remoto válido"}
    return {**base, "classification": "CONFLICT_REVIEW", "action": "REVIEW",
            "reason": "ambos valores existen y son diferentes"}


def plan_mapping(mapping, client, remote, *, duplicate=False, provenance_unverified=False,
                 coverage_complete=False, duplicate_ids_unknown=False):
    """Construye un plan por campo sin guardar clientes, mapas ni auditoría."""
    if client is None:
        blocked_reason = "cliente_local_no_encontrado"
        local = {field: "" for field in FIELDS}
    else:
        blocked_reason = ""
        local = _local_values(client)
    if remote is None:
        values = {field: "" for field in FIELDS}
        identity = {}
    else:
        values, identity = _remote_values(remote)

    if not coverage_complete:
        blocked_reason = "cobertura_remota_incompleta"
    elif duplicate or duplicate_ids_unknown:
        blocked_reason = "mapeo_o_id_externo_duplicado"
    elif remote is not None and client is not None:
        local_identity = normalize_identity(local["identificacion"])
        remote_identity = normalize_identity(values["identificacion"])
        if not local_identity or not remote_identity:
            blocked_reason = "identificacion_no_confirmable"
        elif local_identity != remote_identity:
            blocked_reason = "identificacion_no_coincide"

    invalid_fields = set()
    for field, raw_key in (
        ("regimen_tributario", "raw_regime"),
        ("tipo_identificacion", "raw_type"),
        ("kindOfPerson", "raw_kind"),
    ):
        if identity.get(raw_key) and not values[field]:
            invalid_fields.add(field)

    fields = {}
    for field in FIELDS:
        fields[FIELD_LABELS[field]] = _plan_field(
            field, local[field], values[field], blocked_reason=blocked_reason,
            provenance_unverified=provenance_unverified, invalid=field in invalid_fields,
        )
    classifications = {item["classification"] for item in fields.values()}
    fully_safe = bool(classifications) and classifications.issubset({"SAFE_FILL_CANDIDATE", "ALREADY_MATCHED"})
    return {
        "fields": fields,
        "fully_safe_for_future_apply": fully_safe,
        "record_categories": sorted(classifications),
        "provenance_verified": not provenance_unverified,
    }


INITIAL_IMPORT_FIELDS = (
    "tipo_cliente", "nombre", "razon_social", "identificacion", "tipo_identificacion",
    "digito_verificacion", "regimen_tributario", "email", "telefono", "telefono_secundario",
    "celular", "direccion", "ciudad", "departamento", "pais", "codigo_postal", "activo",
)


def _initial_local_values(client):
    values = {
        field: getattr(client, field, "")
        for field in INITIAL_IMPORT_FIELDS
    }
    values["identificacion"] = normalize_identity(values["identificacion"])
    values["tipo_identificacion"] = str(values["tipo_identificacion"] or "").strip().casefold()
    values["regimen_tributario"] = str(values["regimen_tributario"] or "").strip().upper()
    values["digito_verificacion"] = str(values["digito_verificacion"] if values["digito_verificacion"] is not None else "").strip()
    return values


def _initial_remote_values(row):
    identity = extract_identification_context(row)
    kind = identity.get("kind")
    address = row.get("address") if isinstance(row.get("address"), dict) else {}
    name = str(row.get("name") or "").strip()
    # No se infiere tipo de persona desde NIT u otro documento.
    tipo_cliente = (
        Cliente.TIPO_EMPRESA if kind == "LEGAL_ENTITY"
        else Cliente.TIPO_PERSONA if kind == "PERSON_ENTITY"
        else None
    )
    values = {
        "tipo_cliente": tipo_cliente,
        "nombre": name,
        "razon_social": name if tipo_cliente == Cliente.TIPO_EMPRESA else "",
        "identificacion": normalize_identity(identity.get("number")),
        "tipo_identificacion": str(identity.get("type") or "").strip().casefold(),
        "digito_verificacion": str(identity["dv"]).strip() if identity.get("dv") not in (None, "") else "",
        "regimen_tributario": str(identity.get("regime") or "").strip().upper(),
        "email": str(row.get("email") or "").strip(),
        "telefono": str(row.get("phonePrimary") or "").strip(),
        "telefono_secundario": str(row.get("phoneSecondary") or "").strip(),
        "celular": str(row.get("mobile") or "").strip(),
        "direccion": str(address.get("address") or "").strip(),
        "ciudad": str(address.get("city") or "").strip(),
        "departamento": str(address.get("department") or "").strip(),
        "pais": str(address.get("country") or "").strip(),
        "codigo_postal": str(address.get("zipCode") or "").strip(),
        "activo": (str(row.get("status") or "").casefold() != "inactive") if row.get("status") else None,
    }
    return values, identity


def _compare_initial_value(field, value):
    if field == "identificacion":
        return normalize_identity(value)
    if field == "tipo_identificacion":
        return str(value or "").strip().casefold()
    if field == "regimen_tributario":
        return str(value or "").strip().upper()
    if field == "digito_verificacion":
        return str(value if value is not None else "").strip()
    return value


def _initial_plan_field(field, local, remote, *, blocked_reason="", invalid=False):
    local_value = local.get(field)
    remote_value = remote.get(field)
    local_state = "present" if local_value not in (None, "") else "empty"
    remote_state = "present" if remote_value not in (None, "") else "empty"
    base = {
        "local_state": local_state,
        "remote_state": remote_state,
        "local_value": local_value,
        "remote_value": remote_value,
    }
    if blocked_reason:
        return {**base, "classification": "BLOCKED", "action": "REVIEW", "reason": blocked_reason}
    if invalid:
        return {**base, "classification": "INVALID_REMOTE_VALUE", "action": "REVIEW",
                "reason": "el valor remoto no es compatible con BettaApp"}
    if remote_state == "empty":
        return {**base, "classification": "SIN_DATO_ALEGRA", "action": "KEEP_LOCAL",
                "reason": "Alegra no informa este campo"}
    if _compare_initial_value(field, local_value) == _compare_initial_value(field, remote_value):
        return {**base, "classification": "ALREADY_MATCHED", "action": "NO_ACTION",
                "reason": "los valores coinciden"}
    return {**base, "classification": "UPDATE_FROM_ALEGRA", "action": "UPDATE_LOCAL",
            "reason": "Alegra es la fuente de verdad de la regularización inicial",
            "local_value": local_value, "remote_value": remote_value}


def initial_precedence_plan(mapping, client, remote, *, duplicate=False, coverage_complete=False,
                            duplicate_ids_unknown=False, provenance_unverified=False):
    """Planifica la regularización inicial sin persistir valores ni baselines."""
    local = _initial_local_values(client) if client is not None else {field: "" for field in INITIAL_IMPORT_FIELDS}
    remote_values, identity = _initial_remote_values(remote) if remote is not None else ({field: "" for field in INITIAL_IMPORT_FIELDS}, {})
    blocked_reason = ""
    if client is None:
        blocked_reason = "cliente_local_no_encontrado"
    elif not coverage_complete:
        blocked_reason = "cobertura_remota_incompleta"
    elif duplicate or duplicate_ids_unknown:
        blocked_reason = "mapeo_o_id_externo_duplicado"
    elif remote is None:
        blocked_reason = "contacto_remoto_no_recuperado"
    else:
        local_identity = normalize_identity(local["identificacion"])
        remote_identity = normalize_identity(remote_values["identificacion"])
        if not local_identity or not remote_identity:
            blocked_reason = "identificacion_no_confirmable"
        elif local_identity != remote_identity:
            blocked_reason = "identificacion_no_coincide"

    invalid_fields = set()
    for field, raw_key in (
        ("tipo_cliente", "raw_kind"),
        ("tipo_identificacion", "raw_type"),
        ("regimen_tributario", "raw_regime"),
    ):
        if identity.get(raw_key) and not remote_values[field]:
            invalid_fields.add(field)
    fields = {
        field: _initial_plan_field(field, local, remote_values, blocked_reason=blocked_reason,
                                   invalid=field in invalid_fields)
        for field in INITIAL_IMPORT_FIELDS
    }
    updates = [field for field, detail in fields.items() if detail["classification"] == "UPDATE_FROM_ALEGRA"]
    exceptions = [field for field, detail in fields.items() if detail["classification"] in {"BLOCKED", "INVALID_REMOTE_VALUE"}]
    return {
        "fields": fields,
        "fields_to_update": updates,
        "blocked_fields": exceptions,
        "provenance_exception": bool(provenance_unverified),
        "provenance_reason": "metadato histórico insuficiente; identidad verificada" if provenance_unverified else "",
        "client_blocked": any(detail["classification"] == "BLOCKED" for detail in fields.values()),
        "requires_manual_review": bool(exceptions),
        "writes": False,
    }


class Command(BaseCommand):
    help = "Diagnostica regularización tributaria histórica de Alegra sin modificar datos."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Procesa todos los mapas elegibles.")
        parser.add_argument(
            "--initial-precedence", action="store_true",
            help="Genera el plan dry-run de regularización inicial con precedencia de Alegra.",
        )
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
        fetched_rows = fetched["rows"]
        rows = {str(row.get("id")): row for row in fetched_rows if row.get("id")}
        # El servicio devuelve un contador, no una colección de IDs. Los IDs
        # concretos solo pueden derivarse de las filas recuperadas completas.
        declared_duplicate_count = _duplicate_count(fetched.get("duplicate_external_ids", 0))
        duplicate_ids = _duplicate_ids_from_rows(fetched_rows)
        duplicate_ids_unknown = declared_duplicate_count > 0 and not duplicate_ids
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
        plan_field_counts = {field: Counter() for field in FIELD_LABELS.values()}
        plan_record_counts = Counter()
        clients_with_candidates = 0
        completely_safe_clients = 0
        clients_needing_review = 0
        review_rows = []
        initial_field_counts = {field: Counter() for field in INITIAL_IMPORT_FIELDS}
        initial_field_update_counts = Counter()
        initial_client_counts = Counter()
        initial_provenance_exceptions = 0
        coverage_complete = fetched["coverage"] == "complete" and not fetched["errors"]
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
                or duplicate_ids_unknown
            )
            provenance_unverified = bool(client and mapping.created_at and client.fecha_actualizacion and client.fecha_actualizacion > mapping.created_at)
            if metadata.get("source") != HISTORICAL_SOURCE:
                provenance_unverified = True
            result = diagnose_mapping(mapping, client, remote, duplicate=duplicate, provenance_unverified=provenance_unverified)
            plan = plan_mapping(
                mapping, client, remote, duplicate=duplicate,
                provenance_unverified=provenance_unverified,
                coverage_complete=coverage_complete,
                duplicate_ids_unknown=duplicate_ids_unknown,
            )
            initial_plan = None
            if options["initial_precedence"]:
                initial_plan = initial_precedence_plan(
                    mapping, client, remote, duplicate=duplicate,
                    coverage_complete=coverage_complete,
                    duplicate_ids_unknown=duplicate_ids_unknown,
                    provenance_unverified=provenance_unverified,
                )
                for field, detail in initial_plan["fields"].items():
                    initial_field_counts[field][detail["classification"]] += 1
                    if detail["classification"] == "UPDATE_FROM_ALEGRA":
                        initial_field_update_counts[field] += 1
                if initial_plan["fields_to_update"] and not initial_plan["client_blocked"]:
                    initial_client_counts["eligible"] += 1
                if initial_plan["client_blocked"]:
                    initial_client_counts["blocked"] += 1
                if initial_plan["requires_manual_review"]:
                    initial_client_counts["review"] += 1
                if initial_plan["provenance_exception"]:
                    initial_provenance_exceptions += 1
            plan_record_categories = plan["record_categories"]
            for category in plan_record_categories:
                plan_record_counts[category] += 1
            for field, detail in plan["fields"].items():
                plan_field_counts[field][detail["classification"]] += 1
            if any(detail["classification"] == "SAFE_FILL_CANDIDATE" for detail in plan["fields"].values()):
                clients_with_candidates += 1
            if plan["fully_safe_for_future_apply"]:
                completely_safe_clients += 1
            if any(detail["classification"] not in {"SAFE_FILL_CANDIDATE", "ALREADY_MATCHED"}
                   for detail in plan["fields"].values()):
                clients_needing_review += 1
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
                    "plan": plan,
                })
                if initial_plan is not None:
                    review_rows[-1]["initial_precedence_plan"] = initial_plan
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
            f"ids_duplicados={declared_duplicate_count}; "
            "escrituras_db=0; escrituras_alegra=0."
        )
        self.stdout.write("Categorias de registro: " + ", ".join(f"{key}={value}" for key, value in sorted(aggregate.items())))
        self.stdout.write("Clasificación por campo:")
        for field in FIELD_LABELS.values():
            values = ", ".join(f"{key}={value}" for key, value in sorted(field_counts[field].items())) or "sin_datos=0"
            self.stdout.write(f"- {field}: {values}")
        self.stdout.write(
            f"Plan: candidatos_por_campo={sum(counter.get('SAFE_FILL_CANDIDATE', 0) for counter in plan_field_counts.values())}; "
            f"clientes_con_candidatos={clients_with_candidates}; "
            f"clientes_completamente_seguros_para_futura_aplicacion={completely_safe_clients}; "
            f"clientes_en_revision={clients_needing_review}."
        )
        self.stdout.write("Categorias del plan por cliente: " + ", ".join(
            f"{key}={value}" for key, value in sorted(plan_record_counts.items())
        ) or "sin_datos=0")
        self.stdout.write("Plan por campo:")
        for field in FIELD_LABELS.values():
            values = ", ".join(f"{key}={value}" for key, value in sorted(plan_field_counts[field].items())) or "sin_datos=0"
            self.stdout.write(f"- {field}: {values}")
        if options["initial_precedence"]:
            self.stdout.write(
                "Regularización inicial Alegra (DRY-RUN): "
                f"aptos_para_actualizar_campos_validos={initial_client_counts['eligible']}; "
                f"revision_manual={initial_client_counts['review']}; "
                f"bloqueados={initial_client_counts['blocked']}; "
                f"excepciones_procedencia={initial_provenance_exceptions}; "
                "escrituras_db=0; escrituras_alegra=0."
            )
            self.stdout.write("Cambios propuestos por campo: " + ", ".join(
                f"{field}={count}" for field, count in initial_field_update_counts.items()
            ) or "ninguno")
            self.stdout.write("Regularización inicial por campo:")
            for field in INITIAL_IMPORT_FIELDS:
                values = ", ".join(f"{key}={value}" for key, value in sorted(initial_field_counts[field].items())) or "sin_datos=0"
                self.stdout.write(f"- {field}: {values}")
        if options["review_output"]:
            self.stdout.write(f"Archivo protegido de revisión: {options['review_output']}")

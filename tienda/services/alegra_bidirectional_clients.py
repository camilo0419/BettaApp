"""Planificación segura de sincronización bidireccional de clientes.

Este módulo deliberadamente no contiene métodos de escritura HTTP.  En la fase
7.1 las operaciones hacia Alegra se representan como planes en memoria para
probar identidad, idempotencia y conflictos sin tocar la cuenta externa ni los
registros comerciales locales.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .alegra_contact_import import normalize_identity, normalize_name
from .alegra_normalization import extract_identification_context


PENDING = "PENDING"
SYNCED = "SYNCED"
CONFLICT = "CONFLICT"
FAILED = "FAILED"
NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"

CLASS_LINKED = "linked"
CLASS_PROBABLE = "probable"
CLASS_NEW = "new"
CLASS_CONFLICT = "conflict"
CLASS_INCOMPLETE = "incomplete"

# Registro declarativo: describe capacidades sin habilitar escrituras externas.
INTEGRATION_CAPABILITIES = {
    "contacts": {
        "source_of_truth": "betta_after_initial_import",
        "identity_local": "Cliente.id",
        "identity_external": "Alegra contacts.id",
        "directions": ("alegra_to_betta", "betta_to_alegra"),
        "operations": ("GET", "SIMULATED_CREATE", "SIMULATED_UPDATE"),
        "external_writes_enabled": False,
        "conflict_policy": "manual_review",
        "retry_policy": "idempotency_key_then_reconcile",
        "dependencies": (),
    },
    "items": {
        "source_of_truth": "betta",
        "identity_local": "Producto.id",
        "identity_external": "Alegra items.id",
        "directions": ("alegra_to_betta", "betta_to_alegra"),
        "operations": ("GET", "SIMULATED_CREATE", "SIMULATED_UPDATE"),
        "external_writes_enabled": False,
        "conflict_policy": "manual_review",
        "retry_policy": "read_only",
        "dependencies": (),
    },
    "estimates": {
        "source_of_truth": "betta",
        "identity_local": "Cotizacion.id",
        "identity_external": "Alegra estimates.id",
        "directions": (),
        "operations": ("GET",),
        "external_writes_enabled": False,
        "conflict_policy": "manual_review",
        "retry_policy": "read_only",
        "dependencies": ("contacts", "items"),
    },
    "invoices": {
        "source_of_truth": "alegra",
        "identity_local": "AlegraInvoiceStaging.id",
        "identity_external": "Alegra invoices.id",
        "directions": ("alegra_to_betta",),
        "operations": ("GET",),
        "external_writes_enabled": False,
        "conflict_policy": "financial_source_external",
        "retry_policy": "read_only",
        "dependencies": ("contacts", "items"),
    },
    "payments": {
        "source_of_truth": "alegra",
        "identity_local": "AlegraPaymentStaging.id",
        "identity_external": "Alegra payments.id",
        "directions": ("alegra_to_betta",),
        "operations": ("GET",),
        "external_writes_enabled": False,
        "conflict_policy": "financial_source_external",
        "retry_policy": "read_only",
        "dependencies": ("invoices", "contacts"),
    },
    "receivables": {
        "source_of_truth": "alegra",
        "identity_local": "cartera interna",
        "identity_external": "facturas y pagos Alegra",
        "directions": ("alegra_to_betta",),
        "operations": ("GET", "CALCULATE"),
        "external_writes_enabled": False,
        "conflict_policy": "financial_source_external",
        "retry_policy": "read_only",
        "dependencies": ("invoices", "payments"),
    },
}


@dataclass(frozen=True)
class ClientSyncPlan:
    """Resultado inerte de una operación que todavía no está autorizada."""

    direction: str
    state: str
    idempotency_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    external_id: str = ""
    reason: str = ""
    simulated: bool = True


class SafeAlegraWriteAdapter:
    """Adaptador explícito de simulación; no implementa POST/PUT/PATCH/DELETE."""

    enabled = False

    def create_contact(self, payload: Mapping[str, Any], *, idempotency_key: str) -> ClientSyncPlan:
        return ClientSyncPlan(
            direction="betta_to_alegra",
            state=PENDING,
            idempotency_key=idempotency_key,
            payload=dict(payload),
            reason="Escritura externa desactivada; requiere autorización futura.",
        )

    def create_item(self, payload: Mapping[str, Any], *, idempotency_key: str) -> ClientSyncPlan:
        return ClientSyncPlan(
            direction="betta_to_alegra",
            state=PENDING,
            idempotency_key=idempotency_key,
            payload=dict(payload),
            reason="Creación de producto simulada; no se ejecuta POST externo.",
        )

    def update_item(self, external_id: str, payload: Mapping[str, Any], *, idempotency_key: str) -> ClientSyncPlan:
        return ClientSyncPlan(
            direction="betta_to_alegra",
            state=PENDING,
            idempotency_key=idempotency_key,
            payload={"external_id": str(external_id), **dict(payload)},
            external_id=str(external_id),
            reason="Actualización de producto simulada; no se ejecuta PUT externo.",
        )


class BidirectionalClientSync:
    """Reglas de identidad y planificación sin efectos secundarios."""

    SHARED_FIELDS = (
        "tipo_cliente", "nombre", "razon_social", "tipo_identificacion",
        "identificacion", "digito_verificacion", "email", "telefono",
        "telefono_secundario", "celular", "direccion", "ciudad",
        "departamento", "pais", "codigo_postal",
    )
    EXTERNAL_TO_LOCAL = {
        "name": "nombre",
        "identification": "identificacion",
        "identificationType": "tipo_identificacion",
        "email": "email",
        "phonePrimary": "telefono",
        "phoneSecondary": "telefono_secundario",
        "mobile": "celular",
    }

    def __init__(self, *, writer: SafeAlegraWriteAdapter | None = None):
        self.writer = writer or SafeAlegraWriteAdapter()

    @staticmethod
    def capabilities(resource_type: str | None = None):
        """Devuelve capacidades declarativas; nunca activa una escritura."""
        if resource_type is None:
            return {key: dict(value) for key, value in INTEGRATION_CAPABILITIES.items()}
        return dict(INTEGRATION_CAPABILITIES.get(resource_type, {}))

    @staticmethod
    def _value(source: Any, key: str, default: Any = "") -> Any:
        if isinstance(source, Mapping):
            return source.get(key, default)
        return getattr(source, key, default)

    @staticmethod
    def _external_id(row: Any) -> str:
        return str(BidirectionalClientSync._value(row, "id", "") or BidirectionalClientSync._value(row, "external_id", ""))

    @staticmethod
    def _identity(row: Any) -> str:
        return normalize_identity(BidirectionalClientSync._value(row, "identification", ""))

    @staticmethod
    def _name(row: Any) -> str:
        return normalize_name(BidirectionalClientSync._value(row, "name", ""))

    def classify_initial_import(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        local_clients: Iterable[Any] = (),
        mapped_external_ids: Iterable[str] = (),
    ) -> list[dict[str, Any]]:
        """Clasifica una muestra externa en memoria, sin staging ni cambios locales."""
        rows = list(rows)
        clients = list(local_clients)
        mapped = {str(value) for value in mapped_external_ids}
        external_identity_counts: dict[str, int] = {}
        for row in rows:
            identity = self._identity(row)
            if identity:
                external_identity_counts[identity] = external_identity_counts.get(identity, 0) + 1
        local_by_identity: dict[str, list[Any]] = {}
        for client in clients:
            identity = normalize_identity(self._value(client, "identificacion", ""))
            if identity:
                local_by_identity.setdefault(identity, []).append(client)

        result = []
        for row in rows:
            external_id = self._external_id(row)
            identity = self._identity(row)
            name = self._name(row)
            candidates = local_by_identity.get(identity, []) if identity else []
            if external_id and external_id in mapped:
                classification, reason = CLASS_LINKED, "El ID externo ya tiene un mapeo activo."
            elif not name or not identity:
                classification, reason = CLASS_INCOMPLETE, "Faltan nombre o identificación para revisar la identidad."
            elif external_identity_counts.get(identity, 0) > 1:
                classification, reason = CLASS_CONFLICT, "La identificación aparece en varios contactos externos; no se fusiona automáticamente."
            elif len(candidates) > 1:
                classification, reason = CLASS_CONFLICT, "Hay varios clientes locales con la misma identificación normalizada."
            elif len(candidates) == 1:
                classification, reason = CLASS_PROBABLE, "La identificación coincide con un cliente local; requiere vinculación explícita."
            else:
                classification, reason = CLASS_NEW, "No se encontró una coincidencia local inequívoca."
            result.append({
                "external_id": external_id,
                "classification": classification,
                "reason": reason,
                "candidate_ids": [getattr(candidate, "pk", None) for candidate in candidates],
                "identity_repeated_externally": external_identity_counts.get(identity, 0) > 1 if identity else False,
            })
        return result

    def build_betta_payload(self, client: Any) -> dict[str, Any]:
        """Construye únicamente el payload allowlistado para una futura creación."""
        payload = {}
        for field_name in self.SHARED_FIELDS:
            value = self._value(client, field_name, "")
            if value not in (None, ""):
                payload[field_name] = str(value) if not isinstance(value, (int, float, bool)) else value
        return payload

    @staticmethod
    def idempotency_key(direction: str, stable_id: str, payload: Mapping[str, Any]) -> str:
        raw = json.dumps({"direction": direction, "stable_id": str(stable_id), "payload": payload}, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def plan_betta_creation(self, client: Any) -> ClientSyncPlan:
        payload = self.build_betta_payload(client)
        key = self.idempotency_key("betta_to_alegra", getattr(client, "pk", ""), payload)
        return self.writer.create_contact(payload, idempotency_key=key)

    def pending_local_clients(self, clients: Iterable[Any], mapped_local_ids: Iterable[int] = ()) -> list[ClientSyncPlan]:
        """Prepara operaciones locales sin mapeo; no llama a Alegra."""
        mapped = {int(value) for value in mapped_local_ids if value is not None}
        return [self.plan_betta_creation(client) for client in clients if getattr(client, "pk", None) not in mapped]

    @staticmethod
    def _external_values(row: Mapping[str, Any]) -> dict[str, str]:
        address = row.get("address") if isinstance(row.get("address"), Mapping) else {}
        name_object = row.get("nameObject") if isinstance(row.get("nameObject"), Mapping) else {}
        name = row.get("name", "") or " ".join(
            str(name_object.get(key, "") or "").strip()
            for key in ("firstName", "secondName", "lastName", "secondLastName")
            if str(name_object.get(key, "") or "").strip()
        )
        identity = extract_identification_context(row)
        identification = identity["number"]
        if "-" in identification:
            identification = identification.rsplit("-", 1)[0].strip()
        values = {
            "nombre": name,
            "identificacion": identification,
            "tipo_identificacion": identity["type"] or "",
            "email": row.get("email", ""),
            "telefono": row.get("phonePrimary", ""),
            "telefono_secundario": row.get("phoneSecondary", ""),
            "celular": row.get("mobile", ""),
            "direccion": address.get("address", ""),
            "ciudad": address.get("city", ""),
            "departamento": address.get("department", ""),
            "pais": address.get("country", ""),
            "codigo_postal": address.get("zipCode", ""),
        }
        return {key: str(value or "").strip() for key, value in values.items()}

    @staticmethod
    def _local_values(client: Any) -> dict[str, str]:
        return {field_name: str(getattr(client, field_name, "") or "").strip() for field_name in BidirectionalClientSync.SHARED_FIELDS if field_name != "tipo_cliente"}

    def compare_linked_client(self, client: Any, row: Mapping[str, Any], baseline: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Evalúa cambios sin actualizar el cliente ni afirmar sincronización real."""
        local = self._local_values(client)
        external = self._external_values(row)
        if baseline is None:
            changed = [field for field in external if external[field] and local.get(field, "") != external[field]]
            return {
                "state": SYNCED if not changed else CONFLICT,
                "changed_fields": changed,
                "remote_fields": [],
                "conflict_fields": changed,
                "reason": "Sin cambios observables." if not changed else "No existe baseline confirmado para resolver cambios.",
            }
        normalized_baseline = {key: str(value or "").strip() for key, value in baseline.items()}
        remote_fields, local_fields, conflict_fields = [], [], []
        for field in external:
            base = normalized_baseline.get(field, "")
            remote = external[field]
            current = local.get(field, "")
            if not remote and current:
                continue  # Nunca sustituir un dato local válido por vacío externo.
            remote_changed = remote != base
            local_changed = current != base
            if remote_changed and local_changed and remote != current:
                conflict_fields.append(field)
            elif remote_changed and not local_changed:
                remote_fields.append(field)
            elif local_changed and not remote_changed:
                local_fields.append(field)
        if conflict_fields:
            state = CONFLICT
            reason = "Cambios simultáneos requieren revisión manual."
        elif remote_fields:
            state = PENDING
            reason = "Cambios externos preparados para aplicación local explícita."
        else:
            state = SYNCED
            reason = "Sin cambios externos aplicables."
        return {
            "state": state,
            "changed_fields": sorted(set(remote_fields + local_fields + conflict_fields)),
            "remote_fields": remote_fields,
            "local_fields": local_fields,
            "conflict_fields": conflict_fields,
            "reason": reason,
        }

    def prepare_inbound_updates(self, rows: Iterable[Mapping[str, Any]], mapped_clients: Mapping[str, Any], baselines: Mapping[str, Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
        baselines = baselines or {}
        results = []
        for row in rows:
            external_id = self._external_id(row)
            client = mapped_clients.get(external_id)
            if client is None:
                continue
            comparison = self.compare_linked_client(client, row, baselines.get(external_id))
            results.append({"external_id": external_id, "local_id": client.pk, **comparison})
        return results

    @staticmethod
    def retry_plan(plan: ClientSyncPlan) -> ClientSyncPlan:
        """Los reintentos simulados conservan la identidad y no repiten un POST."""
        if plan.state == NEEDS_RECONCILIATION:
            return plan
        return ClientSyncPlan(
            direction=plan.direction,
            state=PENDING,
            idempotency_key=plan.idempotency_key,
            payload=dict(plan.payload),
            external_id=plan.external_id,
            reason="Reintento simulado con la misma clave idempotente; no se ejecuta escritura externa.",
        )

    def plan_alegra_creation(self, row: Mapping[str, Any]) -> ClientSyncPlan:
        external_id = self._external_id(row)
        payload = {"external_id": external_id, "name": self._value(row, "name", "")}
        key = self.idempotency_key("alegra_to_betta", external_id, payload)
        return ClientSyncPlan(
            direction="alegra_to_betta",
            state=PENDING,
            idempotency_key=key,
            payload=payload,
            external_id=external_id,
            reason="Incorporación local desactivada durante la simulación.",
        )

    def detect_update_conflicts(
        self,
        last_synced: Mapping[str, Any],
        local_values: Mapping[str, Any],
        external_values: Mapping[str, Any],
    ) -> list[str]:
        """Detecta cambios concurrentes sin aplicar una política last-write-wins."""
        conflicts = []
        for field_name in self.SHARED_FIELDS:
            baseline = str(last_synced.get(field_name, "") or "").strip()
            local = str(local_values.get(field_name, "") or "").strip()
            external = str(external_values.get(field_name, "") or "").strip()
            if local != baseline and external != baseline and local != external:
                conflicts.append(field_name)
        return conflicts

    def lost_create_response(self, *, stable_id: str, reason: str = "La respuesta externa pudo perderse.") -> ClientSyncPlan:
        """Bloquea el reintento ciego de una creación cuya respuesta se perdió."""
        return ClientSyncPlan(
            direction="betta_to_alegra",
            state=NEEDS_RECONCILIATION,
            idempotency_key=self.idempotency_key("betta_to_alegra", stable_id, {}),
            reason=reason + " Consultar y conciliar antes de reintentar.",
        )

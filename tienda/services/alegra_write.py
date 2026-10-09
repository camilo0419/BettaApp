"""Adaptador de escritura de contactos Alegra, bloqueado por defecto.

El transporte existe para una futura habilitación explícita, pero ninguna ruta
de la aplicación actual entrega una autorización válida ni activa escrituras.
Las pruebas inyectan un transporte falso y nunca llaman a la red.
"""

from __future__ import annotations

import base64
import io
import json
import os
import uuid
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from tienda.models import AlegraWriteOperation, Cliente, ExternalObjectMap, ExternalSystem
from .alegra_bidirectional_clients import BidirectionalClientSync
from .alegra_client import AlegraConfigurationError, AlegraError, AlegraReadOnlyClient


class ExternalWriteDisabled(AlegraError):
    pass


class ExternalWriteNotAuthorized(AlegraError):
    pass


class AlegraWriteHTTPError(AlegraError):
    def __init__(self, status: int, message: str, *, retryable: bool = False):
        self.status = status
        self.retryable = retryable
        super().__init__(message)


class AlegraWriteResponseError(AlegraError):
    pass


class AlegraWriteUncertain(AlegraError):
    """El resultado pudo perderse; nunca debe repetirse ciegamente."""


class WriteConflict(AlegraError):
    pass


def _safe_http_error_detail(error: HTTPError) -> str:
    """Extrae mensajes de validación sin conservar cuerpos ni datos identificables."""
    try:
        raw = error.read(8192).decode("utf-8", errors="replace")
    except Exception:
        return ""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        data = {}
    values = []
    if isinstance(data, dict):
        for key in ("message", "error", "detail", "code", "errors"):
            value = data.get(key)
            if isinstance(value, (str, int, float)):
                values.append(str(value))
            elif isinstance(value, list):
                values.extend(str(item) for item in value[:5] if isinstance(item, (str, int, float)))
    detail = "; ".join(values).strip()
    if not detail:
        return "respuesta de validación sin detalle estructurado" if raw else ""
    detail = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<redacted-email>", detail)
    detail = re.sub(r"\b\d{5,}\b", "<redacted-number>", detail)
    return detail[:300]


@dataclass(frozen=True)
class WriteAuthorization:
    client_id: int
    operation: str
    environment: str
    confirmed: bool = False
    resource_type: str = "contacts"


def authorize_external_write(*, client_id: int, operation: str, environment: str, confirmed: bool = False, resource_type: str = "contacts") -> WriteAuthorization:
    """Construye una autorización acotada; no activa escrituras por sí misma."""
    return WriteAuthorization(int(client_id), str(operation), str(environment), bool(confirmed), str(resource_type))


class AlegraWriteClient:
    """Transporte HTTP para contactos. POST/PUT solo pasan con autorización explícita."""

    def __init__(self, *, timeout: float | None = None, opener: Callable[..., Any] | None = None):
        self.base_url = os.environ.get("ALEGRA_BASE_URL", "https://api.alegra.com/api/v1").strip().rstrip("/")
        self.email = os.environ.get("ALEGRA_EMAIL", "").strip()
        self.token = os.environ.get("ALEGRA_API_TOKEN", "").strip()
        self.timeout = timeout or float(os.environ.get("ALEGRA_TIMEOUT", "15"))
        self.opener = opener or urlopen
        self.last_status: int | None = None
        if not self.email or not self.token:
            raise AlegraConfigurationError("Faltan credenciales de Alegra.")
        if not self.base_url.startswith(("https://", "http://")):
            raise AlegraConfigurationError("ALEGRA_BASE_URL debe ser HTTP(S).")

    def _auth_header(self) -> str:
        raw = f"{self.email}:{self.token}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _assert_write_allowed(self, authorization: WriteAuthorization | None, *, operation: str, resource_type: str = "contacts"):
        enabled = os.environ.get("ALEGRA_EXTERNAL_WRITES_ENABLED", "").strip().casefold() == "true"
        if not enabled:
            raise ExternalWriteDisabled("Las escrituras externas están deshabilitadas.")
        if not authorization or not authorization.confirmed:
            raise ExternalWriteNotAuthorized("Falta autorización operativa explícita.")
        if authorization.operation != operation or authorization.environment != "production" or authorization.resource_type != resource_type:
            raise ExternalWriteNotAuthorized("La autorización no coincide con la operación o entorno.")

    def _request(self, method: str, path: str, *, payload: Mapping[str, Any] | None = None,
                 authorization: WriteAuthorization | None = None, resource_type: str = "contacts") -> dict[str, Any]:
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            self._assert_write_allowed(authorization, operation=method, resource_type=resource_type)
        url = f"{self.base_url}/{path.lstrip('/')}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(url, data=body, headers={
            "Authorization": self._auth_header(),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }, method=method)
        try:
            with self.opener(request, timeout=self.timeout) as response:
                self.last_status = response.status
                raw = response.read().decode("utf-8")
                data = json.loads(raw) if raw else {}
                return data if isinstance(data, dict) else {"data": data}
        except HTTPError as exc:
            self.last_status = exc.code
            retryable = exc.code in {429, 500, 502, 503}
            detail = _safe_http_error_detail(exc)
            message = f"Alegra respondió HTTP {exc.code}."
            if detail:
                message += f" Detalle: {detail}"
            raise AlegraWriteHTTPError(exc.code, message, retryable=retryable) from None
        except (URLError, TimeoutError, OSError) as exc:
            raise AlegraWriteUncertain(f"Resultado externo incierto: {exc.__class__.__name__}.") from None

    def get_contact(self, external_id: str) -> dict[str, Any]:
        response = AlegraReadOnlyClient(timeout=self.timeout).get(f"/contacts/{external_id}")
        if not isinstance(response.data, dict) or not response.data.get("id"):
            raise AlegraWriteResponseError("La respuesta de detalle no contiene un ID válido.")
        return response.data

    def find_candidates(self, *, identification: str = "", name: str = "") -> list[dict[str, Any]]:
        params = {"type": "client", "mode": "advanced", "limit": 30, "metadata": "true"}
        if identification:
            params["identification"] = identification
        if name:
            params["name"] = name
        response = AlegraReadOnlyClient(timeout=self.timeout).get("/contacts", params)
        data = response.data
        rows = data if isinstance(data, list) else data.get("data", []) if isinstance(data, dict) else []
        return [row for row in rows if isinstance(row, dict)]

    def create_contact(self, payload: Mapping[str, Any], *, authorization: WriteAuthorization) -> dict[str, Any]:
        data = self._request("POST", "/contacts", payload=payload, authorization=authorization)
        if not data.get("id"):
            raise AlegraWriteResponseError("Creación exitosa sin ID externo; requiere conciliación.")
        return data

    def update_contact(self, external_id: str, payload: Mapping[str, Any], *, authorization: WriteAuthorization) -> dict[str, Any]:
        return self._request("PUT", f"/contacts/{external_id}", payload=payload, authorization=authorization)

    def create_item(self, payload: Mapping[str, Any], *, authorization: WriteAuthorization) -> dict[str, Any]:
        self._assert_write_allowed(authorization, operation="POST", resource_type="items")
        data = self._request("POST", "/items", payload=payload, authorization=authorization, resource_type="items")
        if not data.get("id"):
            raise AlegraWriteResponseError("Creación de producto sin ID externo; requiere conciliación.")
        return data

    def update_item(self, external_id: str, payload: Mapping[str, Any], *, authorization: WriteAuthorization) -> dict[str, Any]:
        self._assert_write_allowed(authorization, operation="PUT", resource_type="items")
        return self._request("PUT", f"/items/{external_id}", payload=payload, authorization=authorization, resource_type="items")


COLOMBIA_ID_TYPES = {
    Cliente.ID_CC: "CC",
    Cliente.ID_NIT: "NIT",
    Cliente.ID_CE: "CE",
    Cliente.ID_PASAPORTE: "PP",
}
COLOMBIA_KIND_OF_PERSON = {"LEGAL_ENTITY", "PERSON_ENTITY", "OTHER_ENTITY"}
COLOMBIA_REGIMES = {
    "COMMON_REGIME", "SIMPLIFIED_REGIME", "NATIONAL_CONSUMPTION_TAX",
    "NOT_REPONSIBLE_FOR_CONSUMPTION", "INC_IVA_RESPONSIBLE", "SPECIAL_REGIME",
}


def build_contact_payload(client: Cliente, *, colombia_profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Construye el esquema OpenAPI colombiano con facturación electrónica.

    ``colombia_profile`` debe aportar los datos colombianos que no existen en
    Cliente. No se separan nombres por espacios ni se inventan régimen,
    identificación o dígito de verificación.
    """
    profile = dict(colombia_profile or {})
    if colombia_profile is None:
        profile = {
            "kind_of_person": "PERSON_ENTITY" if client.tipo_cliente == Cliente.TIPO_PERSONA else "LEGAL_ENTITY",
            "regime": client.regimen_tributario,
            "first_name": client.primer_nombre,
            "second_name": client.segundo_nombre,
            "last_name": client.primer_apellido,
            "second_last_name": client.segundo_apellido,
        }
    if not client.identificacion:
        raise ValidationError("Falta el número de identificación colombiano.")
    document_type = COLOMBIA_ID_TYPES.get(client.tipo_identificacion)
    if not document_type:
        raise ValidationError("El tipo de identificación local no es válido para Colombia.")
    kind = str(profile.get("kind_of_person") or "").strip().upper()
    regime = str(profile.get("regime") or "").strip().upper()
    if kind not in COLOMBIA_KIND_OF_PERSON:
        raise ValidationError("Falta un kindOfPerson colombiano válido.")
    if regime not in COLOMBIA_REGIMES:
        raise ValidationError("Falta un régimen colombiano explícito y válido.")
    identification_object: dict[str, Any] = {
        "type": document_type,
        "number": str(client.identificacion).strip(),
    }
    dv = str(client.digito_verificacion or "").strip()
    if dv:
        if document_type != "NIT" or not dv.isdigit() or len(dv) != 1:
            raise ValidationError("El dígito de verificación solo puede ser un dígito para NIT.")
        identification_object["dv"] = dv
    payload: dict[str, Any] = {
        "identificationObject": identification_object,
        "kindOfPerson": kind,
        "regime": regime,
        "type": "client",
        "status": "active" if client.activo else "inactive",
    }
    if kind == "PERSON_ENTITY":
        first_name = str(profile.get("first_name") or "").strip()
        last_name = str(profile.get("last_name") or "").strip()
        if not first_name or not last_name:
            raise ValidationError("Persona natural requiere first_name y last_name explícitos.")
        name_object: dict[str, str] = {"firstName": first_name, "lastName": last_name}
        for source, target in (("second_name", "secondName"), ("second_last_name", "secondLastName")):
            value = str(profile.get(source) or "").strip()
            if value:
                name_object[target] = value
        payload["nameObject"] = name_object
    else:
        legal_name = str(client.razon_social or "").strip()
        if not legal_name:
            raise ValidationError("Persona jurídica requiere razon_social explícita.")
        payload["name"] = legal_name
    mapping = {
        "email": "email", "telefono": "phonePrimary", "telefono_secundario": "phoneSecondary",
        "celular": "mobile",
    }
    for local, external in mapping.items():
        value = str(getattr(client, local, "") or "").strip()
        if value:
            payload[external] = value
    address = {
        "address": client.direccion, "city": client.ciudad, "department": client.departamento,
        "country": client.pais, "zipCode": client.codigo_postal,
    }
    address = {key: str(value).strip() for key, value in address.items() if str(value or "").strip()}
    if address:
        payload["address"] = address
    return payload


class AlegraContactWriteService:
    """Orquesta operación, estado, mapeo y auditoría sin reintentos ciegos."""

    PROTECTED_UPDATE_FIELDS = {
        "tipo_cliente", "tipo_identificacion", "identificacion", "digito_verificacion", "regimen_tributario",
    }

    def __init__(self, *, transport: AlegraWriteClient | None = None):
        self.transport = transport

    def prepare_create(self, client: Cliente, system: ExternalSystem, *,
                       colombia_profile: Mapping[str, Any] | None = None) -> tuple[AlegraWriteOperation, dict[str, Any]]:
        content_type = ContentType.objects.get_for_model(Cliente)
        if ExternalObjectMap.objects.filter(system=system, resource_type="contacts", content_type=content_type, object_id=client.pk, status=ExternalObjectMap.STATUS_ACTIVE).exists():
            raise WriteConflict("El cliente ya tiene un mapeo externo activo.")
        payload = build_contact_payload(client, colombia_profile=colombia_profile)
        key = BidirectionalClientSync.idempotency_key("betta_to_alegra_create", str(client.pk), payload)
        operation, created = AlegraWriteOperation.objects.get_or_create(
            system=system, client=client, operation=AlegraWriteOperation.OP_CREATE,
            idempotency_key=key, defaults={"state": AlegraWriteOperation.STATE_PENDING},
        )
        # A known HTTP failure is retryable, but must remain immutable for audit.
        # Reusing its deterministic key would mutate/replay operation 1 instead of
        # creating an auditable attempt.  Uncertain/sent operations stay blocked.
        if not created and operation.state == AlegraWriteOperation.STATE_FAILED:
            retry_key = f"{key}:retry:{uuid.uuid4().hex}"
            operation = AlegraWriteOperation.objects.create(
                system=system, client=client, operation=AlegraWriteOperation.OP_CREATE,
                idempotency_key=retry_key, state=AlegraWriteOperation.STATE_PENDING,
                result_metadata={"supersedes_operation_id": operation.pk},
            )
        if operation.state == AlegraWriteOperation.STATE_NEEDS_RECONCILIATION:
            raise WriteConflict("La creación anterior requiere conciliación manual.")
        if operation.state == AlegraWriteOperation.STATE_SENT:
            raise WriteConflict("La creación anterior está en curso o requiere verificación.")
        return operation, payload

    def prepare_update(self, client: Cliente, system: ExternalSystem, *, external_id: str,
                       remote_row: Mapping[str, Any], baseline: Mapping[str, Any]) -> tuple[AlegraWriteOperation, dict[str, Any]]:
        """Prepara solo cambios no conflictivos y no vacíos."""
        content_type = ContentType.objects.get_for_model(Cliente)
        mapping = ExternalObjectMap.objects.filter(
            system=system, resource_type="contacts", external_id=str(external_id),
            content_type=content_type, object_id=client.pk, status=ExternalObjectMap.STATUS_ACTIVE,
        ).first()
        if not mapping:
            raise WriteConflict("La actualización requiere un mapeo activo del cliente.")
        comparison = BidirectionalClientSync().compare_linked_client(client, remote_row, baseline)
        if comparison["conflict_fields"]:
            raise WriteConflict("Existen cambios simultáneos; se requiere revisión manual.")
        local_fields = [field for field in comparison["local_fields"] if field not in self.PROTECTED_UPDATE_FIELDS]
        if not local_fields:
            raise ValidationError("No hay cambios locales autorizables.")
        context = self._build_update_context(remote_row)
        payload = self._build_update_payload(client, local_fields, external_context=context)
        if not payload:
            raise ValidationError("No hay campos compatibles para actualizar.")
        key = BidirectionalClientSync.idempotency_key("betta_to_alegra_update", str(external_id), payload)
        operation, _ = AlegraWriteOperation.objects.get_or_create(
            system=system, client=client, operation=AlegraWriteOperation.OP_UPDATE,
            idempotency_key=key,
            defaults={"external_id": str(external_id), "state": AlegraWriteOperation.STATE_PENDING,
                      "result_metadata": {
                          "fields": local_fields,
                          "preview": {"fields": local_fields, "payload": payload},
                          "prepared_payload": payload,
                          "external_context": context,
                      }},
        )
        if operation.state == AlegraWriteOperation.STATE_NEEDS_RECONCILIATION:
            raise WriteConflict("La actualización anterior requiere conciliación manual.")
        return operation, payload

    @staticmethod
    def _build_update_context(remote_row: Mapping[str, Any]) -> dict[str, Any]:
        identification_object = remote_row.get("identificationObject")
        if not isinstance(identification_object, Mapping):
            raise ValidationError("La respuesta externa no contiene identificationObject para actualizar el contacto.")
        document_type = str(identification_object.get("type") or "").strip()
        document_number = str(identification_object.get("number") or "").strip()
        if not document_type or not document_number:
            raise ValidationError("La respuesta externa no contiene tipo y número de identificación completos.")

        identification = str(remote_row.get("identification") or document_number).strip()
        kind_of_person = str(remote_row.get("kindOfPerson") or "").strip().upper()
        regime = str(remote_row.get("regime") or "").strip().upper()
        contact_type = remote_row.get("type")
        if isinstance(contact_type, list):
            contact_type = "client" if "client" in contact_type else (contact_type[0] if contact_type else "")
        contact_type = str(contact_type or "").strip().lower()
        status = str(remote_row.get("status") or "").strip().lower()
        if not identification or not kind_of_person or not regime or not contact_type or status not in {"active", "inactive"}:
            raise ValidationError("La respuesta externa no contiene el contexto colombiano obligatorio para actualizar el contacto.")

        context_identification = {"type": document_type, "number": document_number}
        if str(identification_object.get("dv") or "").strip():
            context_identification["dv"] = str(identification_object["dv"]).strip()
        return {
            "identification": identification,
            "identificationObject": context_identification,
            "kindOfPerson": kind_of_person,
            "regime": regime,
            "type": contact_type,
            "status": status,
        }

    @staticmethod
    def _build_update_payload(
        client: Cliente,
        fields: list[str],
        *,
        external_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if external_context is None:
            raise ValidationError("La actualización requiere el contexto colombiano confirmado mediante GET.")
        payload: dict[str, Any] = dict(external_context)
        simple = {"email": "email", "telefono": "phonePrimary", "telefono_secundario": "phoneSecondary", "celular": "mobile"}
        if client.tipo_cliente == Cliente.TIPO_PERSONA:
            if not client.nombre_mostrado:
                raise ValidationError("La actualización requiere un nombre de contacto.")
            payload["name"] = client.nombre_mostrado
        elif client.razon_social:
            payload["name"] = client.razon_social.strip()
        elif client.nombre:
            payload["name"] = client.nombre.strip()
        if any(field in fields for field in {"nombre", "primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido", "razon_social"}):
            if client.tipo_cliente == Cliente.TIPO_PERSONA:
                first_name = str(client.primer_nombre or "").strip()
                last_name = str(client.primer_apellido or "").strip()
                if not first_name or not last_name:
                    raise ValidationError("La actualización de persona natural requiere nombres y apellidos completos.")
                name_object = {"firstName": first_name, "lastName": last_name}
                if client.segundo_nombre:
                    name_object["secondName"] = client.segundo_nombre.strip()
                if client.segundo_apellido:
                    name_object["secondLastName"] = client.segundo_apellido.strip()
                payload["nameObject"] = name_object
            elif client.razon_social:
                payload["name"] = client.razon_social.strip()
        for field in fields:
            if field in simple:
                value = str(getattr(client, field, "") or "").strip()
                if value:
                    payload[simple[field]] = value
        address_fields = {"direccion": "address", "ciudad": "city", "departamento": "department",
                          "pais": "country", "codigo_postal": "zipCode"}
        if any(field in address_fields for field in fields):
            address = {key: str(getattr(client, field, "") or "").strip()
                       for field, key in address_fields.items()}
            payload["address"] = {key: value for key, value in address.items() if value}
        return payload

    def execute_create(self, operation_id: int, *, authorization: WriteAuthorization,
                       colombia_profile: Mapping[str, Any] | None = None) -> AlegraWriteOperation:
        if not self.transport:
            self.transport = AlegraWriteClient()
        with transaction.atomic():
            operation = AlegraWriteOperation.objects.select_for_update().select_related("client", "system").get(pk=operation_id)
            if operation.state == AlegraWriteOperation.STATE_SYNCED:
                return operation
            if operation.state == AlegraWriteOperation.STATE_NEEDS_RECONCILIATION:
                raise WriteConflict("La operación requiere conciliación manual.")
            operation.attempts += 1
            operation.last_attempt_at = timezone.now()
            operation.state = AlegraWriteOperation.STATE_SENT
            operation.save(update_fields=["attempts", "last_attempt_at", "state", "updated_at"])
            client = Cliente.objects.select_for_update().get(pk=operation.client_id)
            system = operation.system
        try:
            response = self.transport.create_contact(
                build_contact_payload(client, colombia_profile=colombia_profile),
                authorization=authorization,
            )
            external_id = str(response.get("id") or "").strip()
            if not external_id:
                raise AlegraWriteResponseError("Respuesta sin ID externo.")
            with transaction.atomic():
                operation = AlegraWriteOperation.objects.select_for_update().get(pk=operation_id)
                existing = ExternalObjectMap.objects.filter(system=system, resource_type="contacts", external_id=external_id, status=ExternalObjectMap.STATUS_ACTIVE).first()
                if existing and (existing.object_id != client.pk or existing.content_type_id != ContentType.objects.get_for_model(Cliente).pk):
                    operation.state = AlegraWriteOperation.STATE_NEEDS_RECONCILIATION
                    operation.reconciliation_reason = "El ID externo ya pertenece a otro objeto local."
                    operation.save(update_fields=["state", "reconciliation_reason", "updated_at"])
                    return operation
                if not existing:
                    ExternalObjectMap.objects.create(
                        system=system, resource_type="contacts", external_id=external_id,
                        content_type=ContentType.objects.get_for_model(Cliente), object_id=client.pk,
                        status=ExternalObjectMap.STATUS_ACTIVE, last_synced_at=timezone.now(),
                        metadata={"source": "alegra_write", "last_synced_fields": BidirectionalClientSync().build_betta_payload(client)},
                    )
                operation.external_id = external_id
                operation.state = AlegraWriteOperation.STATE_SYNCED
                operation.result_metadata = {"response_id_present": True}
                operation.save(update_fields=["external_id", "state", "result_metadata", "updated_at"])
                return operation
        except AlegraWriteUncertain as exc:
            return self._mark_uncertain(operation_id, str(exc))
        except AlegraWriteResponseError as exc:
            return self._mark_uncertain(operation_id, str(exc))
        except (AlegraError, ValidationError, IntegrityError) as exc:
            return self._mark_failed(operation_id, str(exc))

    def _mark_uncertain(self, operation_id: int, message: str) -> AlegraWriteOperation:
        return self._mark(operation_id, AlegraWriteOperation.STATE_NEEDS_RECONCILIATION, message, "uncertain")

    def _mark_failed(self, operation_id: int, message: str) -> AlegraWriteOperation:
        return self._mark(operation_id, AlegraWriteOperation.STATE_FAILED, message, "failed")

    @staticmethod
    def _mark(operation_id: int, state: str, message: str, code: str) -> AlegraWriteOperation:
        operation = AlegraWriteOperation.objects.get(pk=operation_id)
        operation.state = state
        operation.error_code = code
        operation.error_message = str(message)[:500]
        operation.save(update_fields=["state", "error_code", "error_message", "updated_at"])
        return operation

    def execute_update(self, operation_id: int, *, authorization: WriteAuthorization) -> AlegraWriteOperation:
        if not self.transport:
            self.transport = AlegraWriteClient()
        with transaction.atomic():
            operation = AlegraWriteOperation.objects.select_for_update().select_related("system").get(pk=operation_id)
            if operation.state == AlegraWriteOperation.STATE_SYNCED:
                return operation
            if operation.state == AlegraWriteOperation.STATE_NEEDS_RECONCILIATION:
                raise WriteConflict("La actualización requiere conciliación manual.")
            client = Cliente.objects.get(pk=operation.client_id)
            metadata = operation.result_metadata or {}
            payload = self._build_update_payload(
                client,
                metadata.get("fields", []),
                external_context=metadata.get("external_context"),
            )
            prepared_payload = (operation.result_metadata or {}).get("prepared_payload")
            if prepared_payload is not None and payload != prepared_payload:
                operation.state = AlegraWriteOperation.STATE_CONFLICT
                operation.reconciliation_reason = "Los datos locales cambiaron después de preparar la actualización."
                operation.save(update_fields=["state", "reconciliation_reason", "updated_at"])
                return operation
            operation.attempts += 1
            operation.last_attempt_at = timezone.now()
            operation.state = AlegraWriteOperation.STATE_SENT
            operation.save(update_fields=["attempts", "last_attempt_at", "state", "updated_at"])
        try:
            response = self.transport.update_contact(operation.external_id, payload, authorization=authorization)
            confirmed = self.transport.get_contact(operation.external_id)
            if str(confirmed.get("id")) != str(operation.external_id):
                raise AlegraWriteResponseError("La lectura posterior no confirmó el contacto actualizado.")
            operation = AlegraWriteOperation.objects.get(pk=operation_id)
            operation.state = AlegraWriteOperation.STATE_SYNCED
            operation.result_metadata = {"response_id_present": bool(response.get("id") or operation.external_id), "confirmed_by_get": True}
            operation.save(update_fields=["state", "result_metadata", "updated_at"])
            mapping = ExternalObjectMap.objects.filter(
                system=operation.system, resource_type="contacts", external_id=operation.external_id,
                content_type=ContentType.objects.get_for_model(Cliente), object_id=operation.client_id,
                status=ExternalObjectMap.STATUS_ACTIVE,
            ).first()
            if mapping:
                mapping.metadata = {
                    **(mapping.metadata or {}),
                    "last_synced_fields": BidirectionalClientSync().build_betta_payload(operation.client),
                }
                mapping.last_synced_at = timezone.now()
                mapping.save(update_fields=["metadata", "last_synced_at"])
            return operation
        except AlegraWriteUncertain as exc:
            return self._mark_uncertain(operation_id, str(exc))
        except (AlegraError, ValidationError, IntegrityError) as exc:
            return self._mark_failed(operation_id, str(exc))

"""Diagnóstico de solo lectura para la comparación de nombre de un contacto."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError

from tienda.models import AlegraWriteOperation, Cliente, ExternalObjectMap
from tienda.services.alegra_bidirectional_clients import BidirectionalClientSync
from tienda.services.alegra_client import AlegraError, AlegraReadOnlyClient
from tienda.services.alegra_import import get_alegra_system
from tienda.services.alegra_contact_import import normalize_name


class Command(BaseCommand):
    help = "Diagnostica la comparación de nombre de un cliente vinculado sin exponer nombres ni escribir datos."

    def add_arguments(self, parser):
        parser.add_argument("--client-id", type=int, required=True)
        parser.add_argument("--operation-id", type=int)
        parser.add_argument("--timeout", type=float, default=15)

    @staticmethod
    def _fingerprint(value):
        normalized = normalize_name(value)
        return {
            "present": bool(normalized),
            "length": len(normalized),
            "sha256_12": hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12] if normalized else None,
        }

    @staticmethod
    def _remote_name(remote):
        if "name" in remote:
            return remote.get("name"), "name"
        name_object = remote.get("nameObject")
        if isinstance(name_object, Mapping):
            value = " ".join(
                str(name_object.get(key) or "").strip()
                for key in ("firstName", "secondName", "lastName", "secondLastName")
                if str(name_object.get(key) or "").strip()
            )
            return value, "nameObject"
        return "", "missing"

    def handle(self, *args, **options):
        client = Cliente.objects.filter(pk=options["client_id"]).first()
        if client is None:
            raise CommandError("No existe el cliente indicado.")
        content_type = ContentType.objects.get_for_model(Cliente)
        mappings = list(ExternalObjectMap.objects.filter(
            system=get_alegra_system(), resource_type="contacts",
            content_type=content_type, object_id=client.pk,
            status=ExternalObjectMap.STATUS_ACTIVE,
        ).order_by("id"))
        if len(mappings) != 1:
            raise CommandError(f"El cliente requiere exactamente un mapeo activo; encontrados={len(mappings)}.")
        mapping = mappings[0]

        metadata = mapping.metadata if isinstance(mapping.metadata, Mapping) else {}
        operation = None
        if options.get("operation_id") is not None:
            operation = AlegraWriteOperation.objects.filter(
                pk=options["operation_id"], client_id=client.pk,
            ).first()
            if operation is None:
                raise CommandError("La operación indicada no pertenece al cliente.")
            operation_metadata = operation.result_metadata if isinstance(operation.result_metadata, Mapping) else {}
            baseline = operation_metadata.get("baseline")
            local_value = operation_metadata.get("local_snapshot", {}).get("nombre") if isinstance(operation_metadata.get("local_snapshot"), Mapping) else None
            baseline_source = operation_metadata.get("baseline_source") or metadata.get("baseline_source")
        else:
            baseline = metadata.get("last_confirmed") or metadata.get("last_synced_fields")
            local_value = client.nombre_mostrado
            baseline_source = metadata.get("baseline_source")
        if not isinstance(baseline, Mapping):
            baseline = {}
        if local_value is None:
            local_value = client.nombre_mostrado
        try:
            response = AlegraReadOnlyClient(timeout=options["timeout"]).get(f"/contacts/{mapping.external_id}")
        except AlegraError as exc:
            raise CommandError(f"GET de diagnóstico falló: {exc.__class__.__name__}.") from exc
        remote = response.data if isinstance(response.data, Mapping) else {}
        if str(remote.get("id") or "") != str(mapping.external_id):
            raise CommandError("El GET no confirmó el ID externo del mapeo.")
        remote_value, remote_source = self._remote_name(remote)
        values = {
            "client_id": client.pk,
            "mapping_id": mapping.pk,
            "external_id_present": bool(mapping.external_id),
            "operation_id": operation.pk if operation else None,
            "baseline_source": baseline_source or "",
            "baseline": self._fingerprint(baseline.get("nombre", "")),
            "local": self._fingerprint(local_value),
            "remote": self._fingerprint(remote_value),
            "remote_representation": remote_source,
            "remote_name_present": "name" in remote,
            "remote_name_object_present": isinstance(remote.get("nameObject"), Mapping),
            "local_equals_baseline": normalize_name(local_value) == normalize_name(baseline.get("nombre", "")),
            "remote_equals_baseline": normalize_name(remote_value) == normalize_name(baseline.get("nombre", "")),
            "local_equals_remote": normalize_name(local_value) == normalize_name(remote_value),
        }
        self.stdout.write(json.dumps(values, ensure_ascii=False, sort_keys=True))

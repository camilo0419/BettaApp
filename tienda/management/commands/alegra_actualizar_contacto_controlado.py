"""Actualización puntual y auditada de un contacto Alegra existente.

Dry-run por defecto. La operación no reutiliza ni modifica AlegraWriteOperation;
está destinada a una recuperación controlada de un contacto ya vinculado.
"""

from __future__ import annotations

import json

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError

from tienda.models import Cliente, ExternalObjectMap, SyncAuditLog
from tienda.services.alegra_operation_queue import alegra_system
from tienda.services.alegra_write import (
    AlegraContactWriteService,
    AlegraError,
    AlegraWriteClient,
    authorize_external_write,
)


CONFIRMATION = "AUTORIZAR ACTUALIZACION CONTACTO 678"
TARGET_FIELDS = ("email", "telefono", "telefono_secundario", "ciudad", "departamento", "pais")
EXPECTED_CLIENT_ID = 165
EXPECTED_EXTERNAL_ID = "678"
EXPECTED_VALUES = {
    "email": "test@gmail.com",
    "telefono": "5555555",
    "telefono_secundario": "3334455",
    "ciudad": "MEDELLÍN",
    "departamento": "ANTIOQUIA",
    "pais": "Colombia",
}


class Command(BaseCommand):
    help = "Previsualiza o actualiza una vez el contacto Alegra 678 ya vinculado al cliente 165."

    def add_arguments(self, parser):
        parser.add_argument("--client-id", type=int, default=EXPECTED_CLIENT_ID)
        parser.add_argument("--external-id", default=EXPECTED_EXTERNAL_ID)
        parser.add_argument("--timeout", type=float, default=15)
        parser.add_argument("--execute", action="store_true", help="Autoriza un único PUT después de todas las validaciones.")
        parser.add_argument("--confirm", default="")

    @staticmethod
    def _local_values(client):
        return {
            "email": str(client.email or "").strip(),
            "telefono": str(client.telefono or "").strip(),
            "telefono_secundario": str(client.telefono_secundario or "").strip(),
            "ciudad": str(client.ciudad or "").strip(),
            "departamento": str(client.departamento or "").strip(),
            "pais": str(client.pais or "").strip(),
        }

    @staticmethod
    def _protected_matches(client, context):
        expected_type = {Cliente.ID_CC: "CC", Cliente.ID_NIT: "NIT", Cliente.ID_CE: "CE", Cliente.ID_PASAPORTE: "PP"}.get(client.tipo_identificacion)
        expected_kind = "PERSON_ENTITY" if client.tipo_cliente == Cliente.TIPO_PERSONA else "LEGAL_ENTITY"
        identification_object = context.get("identificationObject", {})
        checks = {
            "identification_type": (identification_object.get("type"), expected_type),
            "identification_number": (identification_object.get("number"), str(client.identificacion or "").strip()),
            "digito_verificacion": (identification_object.get("dv") or "", str(client.digito_verificacion or "").strip()),
            "kindOfPerson": (context.get("kindOfPerson"), expected_kind),
            "regime": (context.get("regime"), str(client.regimen_tributario or "").strip().upper()),
        }
        return [field for field, (remote, local) in checks.items() if str(remote or "").strip() != str(local or "").strip()]

    @staticmethod
    def _field_value(confirmed, field):
        address = confirmed.get("address") if isinstance(confirmed.get("address"), dict) else {}
        key = {
            "email": "email", "telefono": "phonePrimary", "telefono_secundario": "phoneSecondary",
            "ciudad": "city", "departamento": "department", "pais": "country",
        }[field]
        if field in {"ciudad", "departamento", "pais"}:
            return key in address, str(address.get(key) or "").strip()
        return key in confirmed, str(confirmed.get(key) or "").strip()

    def _load_target(self, client_id, external_id):
        try:
            client = Cliente.objects.get(pk=client_id)
        except Cliente.DoesNotExist as exc:
            raise CommandError("No existe el cliente local indicado.") from exc
        mappings = list(ExternalObjectMap.objects.filter(
            system=alegra_system(), resource_type="contacts", external_id=str(external_id),
            content_type=ContentType.objects.get_for_model(Cliente), object_id=client.pk,
            status=ExternalObjectMap.STATUS_ACTIVE,
        ))
        if len(mappings) != 1:
            raise CommandError(f"Se requiere exactamente un vínculo activo; encontrados={len(mappings)}.")
        return client, mappings[0]

    def handle(self, *args, **options):
        client_id = options["client_id"]
        external_id = str(options["external_id"])
        if client_id != EXPECTED_CLIENT_ID or external_id != EXPECTED_EXTERNAL_ID:
            raise CommandError("Esta herramienta está restringida al cliente 165 y contacto 678.")
        client, mapping = self._load_target(client_id, external_id)
        local = self._local_values(client)
        if local != EXPECTED_VALUES:
            raise CommandError("Los valores locales no coinciden con la lista autorizada; no se realiza ninguna llamada de escritura.")

        transport = AlegraWriteClient(timeout=options["timeout"])
        service = AlegraContactWriteService(transport=transport)
        try:
            remote = transport.get_contact(external_id)
            if str(remote.get("id")) != external_id:
                raise CommandError("El GET no confirmó el ID externo 678.")
            context = service._build_update_context(remote)
            protected = self._protected_matches(client, context)
            expected_name_object = {
                "firstName": str(client.primer_nombre or "").strip(),
                "lastName": str(client.primer_apellido or "").strip(),
            }
            if client.segundo_nombre:
                expected_name_object["secondName"] = client.segundo_nombre.strip()
            if client.segundo_apellido:
                expected_name_object["secondLastName"] = client.segundo_apellido.strip()
            remote_name_object = remote.get("nameObject")
            if remote_name_object != expected_name_object:
                protected.append("nameObject")
            if str(remote.get("name") or "").strip() != str(client.nombre_mostrado or "").strip():
                protected.append("name")
            if protected:
                raise CommandError("Campos protegidos diferentes: " + ", ".join(protected))
            payload = service._build_update_payload(client, list(TARGET_FIELDS), external_context=context)
        except (AlegraError, ValueError, KeyError) as exc:
            raise CommandError(f"GET o construcción segura bloqueada: {exc}") from exc

        differences = {}
        for field in TARGET_FIELDS:
            present, remote_value = self._field_value(remote, field)
            if not present or remote_value != local[field]:
                differences[field] = {"remote_present": present, "different": remote_value != local[field]}
        self.stdout.write("Vista previa (sin credenciales):")
        self.stdout.write(json.dumps({"external_id": external_id, "fields": TARGET_FIELDS, "payload": payload, "differences": differences}, ensure_ascii=False, indent=2))
        if not options["execute"]:
            self.stdout.write("Modo dry-run; no se ejecutó PUT.")
            return
        if options["confirm"] != CONFIRMATION:
            raise CommandError(f'--execute requiere --confirm "{CONFIRMATION}".')

        authorization = authorize_external_write(
            client_id=client.pk, operation="PUT", environment="production", confirmed=True,
        )
        try:
            response = transport.update_contact(external_id, payload, authorization=authorization)
            confirmed = transport.get_contact(external_id)
            if str(confirmed.get("id")) != external_id:
                raise CommandError("El GET posterior no confirmó el contacto 678.")
            missing = []
            mismatched = []
            for field in TARGET_FIELDS:
                present, value = self._field_value(confirmed, field)
                if not present:
                    missing.append(field)
                elif value != local[field]:
                    mismatched.append(field)
            if missing or mismatched:
                SyncAuditLog.objects.create(
                    system=mapping.system, operation="controlled_contact_update", resource="contacts",
                    external_id=external_id, result=SyncAuditLog.RESULT_PARTIAL,
                    detail="PUT ejecutado; GET posterior incompleto o contradictorio.",
                    metadata={"client_id": client.pk, "fields": list(TARGET_FIELDS), "missing": missing, "mismatched": mismatched},
                )
                raise CommandError("PUT aplicado pero no confirmado completamente; no se repite automáticamente.")
            SyncAuditLog.objects.create(
                system=mapping.system, operation="controlled_contact_update", resource="contacts",
                external_id=external_id, result=SyncAuditLog.RESULT_SUCCESS,
                detail="Actualización puntual confirmada mediante GET posterior.",
                metadata={"client_id": client.pk, "fields": list(TARGET_FIELDS), "response_id_present": bool(response.get("id"))},
            )
            self.stdout.write("PUT único confirmado mediante GET posterior.")
        except CommandError:
            raise
        except AlegraError as exc:
            SyncAuditLog.objects.create(
                system=mapping.system, operation="controlled_contact_update", resource="contacts",
                external_id=external_id, result=SyncAuditLog.RESULT_ERROR,
                detail="La actualización puntual falló; requiere revisión y no se repite automáticamente.",
                metadata={"client_id": client.pk, "fields": list(TARGET_FIELDS), "error_type": exc.__class__.__name__},
            )
            raise CommandError(f"Actualización no confirmada: {exc}") from exc

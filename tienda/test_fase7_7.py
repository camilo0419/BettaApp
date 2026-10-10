import os
import io
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.test import TestCase
from io import StringIO

from tienda.models import AlegraWriteOperation, Cliente, ExternalObjectMap
from tienda.services.alegra_import import get_alegra_system
from tienda.services.alegra_client import AlegraResponse, AlegraPaginationError
from tienda.services.alegra_write import (
    AlegraContactWriteService,
    AlegraWriteClient,
    AlegraWriteResponseError,
    AlegraWriteHTTPError,
    AlegraWriteUncertain,
    ExternalWriteDisabled,
    WriteConflict,
    authorize_external_write,
    build_contact_payload,
)


class FakeTransport:
    def __init__(self, create_result=None, update_result=None, detail=None, create_error=None, update_error=None, candidates=None):
        self.create_result = {"id": "A-NEW"} if create_result is None else create_result
        self.update_result = {"id": "A-1"} if update_result is None else update_result
        self.detail = {"id": "A-1", "name": "Actualizado"} if detail is None else detail
        self.create_error = create_error
        self.update_error = update_error
        self.candidates = candidates or []
        self.create_calls = []
        self.update_calls = []

    def find_candidates(self, *, identification="", name=""):
        return list(self.candidates)

    def create_contact(self, payload, *, authorization):
        self.create_calls.append((payload, authorization))
        if self.create_error:
            raise self.create_error
        return self.create_result

    def update_contact(self, external_id, payload, *, authorization):
        self.update_calls.append((external_id, payload, authorization))
        if self.update_error:
            raise self.update_error
        return self.update_result

    def get_contact(self, external_id):
        return {**self.detail, "id": external_id}


class AlegraWriteAdapterTests(TestCase):
    def setUp(self):
        self.system = get_alegra_system()
        self.client = Cliente.objects.create(
            nombre="Cliente local", razon_social="Cliente Local SAS", tipo_cliente=Cliente.TIPO_EMPRESA,
            identificacion="900123456", tipo_identificacion="nit", digito_verificacion="1",
            email="local@example.test", telefono="3000000000", direccion="Calle 1", ciudad="Bogota",
        )
        self.profile = {"kind_of_person": "LEGAL_ENTITY", "regime": "COMMON_REGIME"}
        self.authorization = authorize_external_write(
            client_id=self.client.pk, operation="POST", environment="production", confirmed=True,
        )

    def test_payload_is_allowlisted_and_omits_empty_values(self):
        self.client.proyecto_interno = "no debe salir"
        payload = build_contact_payload(self.client, colombia_profile=self.profile)
        self.assertEqual(payload["type"], "client")
        self.assertEqual(payload["identificationObject"], {"type": "NIT", "number": "900123456", "dv": "1"})
        self.assertEqual(payload["kindOfPerson"], "LEGAL_ENTITY")
        self.assertEqual(payload["regime"], "COMMON_REGIME")
        self.assertEqual(payload["name"], "Cliente Local SAS")
        self.assertNotIn("proyecto_interno", payload)
        self.assertNotIn("internalContacts", payload)

    def test_natural_person_requires_explicit_names_and_uses_colombia_schema(self):
        client = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Nombre almacenado completo",
            identificacion="123456789", tipo_identificacion="cc",
        )
        profile = {"kind_of_person": "PERSON_ENTITY", "regime": "COMMON_REGIME",
                   "first_name": "Nombre", "last_name": "Apellido", "second_name": "Segundo"}
        payload = build_contact_payload(client, colombia_profile=profile)
        self.assertEqual(payload["nameObject"], {"firstName": "Nombre", "lastName": "Apellido", "secondName": "Segundo"})
        self.assertNotIn("name", payload)
        self.assertEqual(payload["identificationObject"], {"type": "CC", "number": "123456789"})

    def test_natural_person_without_explicit_names_is_blocked(self):
        client = Cliente.objects.create(tipo_cliente=Cliente.TIPO_PERSONA, nombre="Nombre completo",
                                         identificacion="123456789", tipo_identificacion="cc")
        with self.assertRaises(ValidationError):
            build_contact_payload(client, colombia_profile={"kind_of_person": "PERSON_ENTITY", "regime": "COMMON_REGIME"})

    def test_company_without_legal_name_is_blocked(self):
        client = Cliente.objects.create(tipo_cliente=Cliente.TIPO_EMPRESA, nombre="Referencia",
                                         identificacion="900123457", tipo_identificacion="nit")
        with self.assertRaises(ValidationError):
            build_contact_payload(client, colombia_profile=self.profile)

    def test_invalid_identification_type_is_blocked(self):
        self.client.tipo_identificacion = Cliente.ID_OTRO
        with self.assertRaises(ValidationError):
            build_contact_payload(self.client, colombia_profile=self.profile)

    def test_missing_regime_is_blocked(self):
        with self.assertRaises(ValidationError):
            build_contact_payload(self.client, colombia_profile={"kind_of_person": "LEGAL_ENTITY"})

    def test_external_write_is_disabled_and_does_not_call_network(self):
        with patch.dict(os.environ, {"ALEGRA_EMAIL": "test@example.test", "ALEGRA_API_TOKEN": "secret"}, clear=False):
            opener = Mock()
            http = AlegraWriteClient(opener=opener)
            with self.assertRaises(ExternalWriteDisabled):
                http.create_contact({"name": "x", "identification": "1", "type": "client"}, authorization=self.authorization)
            opener.assert_not_called()

    def test_http_400_keeps_only_sanitized_validation_detail(self):
        error = HTTPError(
            "https://example.invalid/contacts", 400, "Bad Request", {},
            io.BytesIO(b'{"message":"identification 900123456 is invalid","email":"x@example.com"}'),
        )
        opener = Mock(side_effect=error)
        with patch.dict(os.environ, {"ALEGRA_EMAIL": "test@example.test", "ALEGRA_API_TOKEN": "secret", "ALEGRA_EXTERNAL_WRITES_ENABLED": "true"}, clear=False):
            http = AlegraWriteClient(opener=opener)
            with self.assertRaises(AlegraWriteHTTPError) as raised:
                http.create_contact({"name": "x", "identification": "1", "type": "client"}, authorization=self.authorization)
        self.assertEqual(raised.exception.status, 400)
        self.assertEqual(http.last_status, 400)
        self.assertIn("identification", str(raised.exception))
        self.assertNotIn("900123456", str(raised.exception))
        self.assertNotIn("x@example.com", str(raised.exception))

    def test_prepare_create_is_idempotent_and_existing_map_blocks(self):
        service = AlegraContactWriteService(transport=FakeTransport())
        first, payload = service.prepare_create(self.client, self.system, colombia_profile=self.profile)
        second, _ = service.prepare_create(self.client, self.system, colombia_profile=self.profile)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.idempotency_key, second.idempotency_key)
        self.assertIn("name", payload)
        ct = ContentType.objects.get_for_model(Cliente)
        ExternalObjectMap.objects.create(system=self.system, resource_type="contacts", external_id="A-1", content_type=ct, object_id=self.client.pk)
        with self.assertRaises(WriteConflict):
            service.prepare_create(self.client, self.system, colombia_profile=self.profile)

    def test_failed_create_gets_new_attempt_without_mutating_history(self):
        service = AlegraContactWriteService(transport=FakeTransport())
        first, _ = service.prepare_create(self.client, self.system, colombia_profile=self.profile)
        first.state = AlegraWriteOperation.STATE_FAILED
        first.attempts = 1
        first.error_code = "failed"
        first.error_message = "HTTP 400"
        first.save(update_fields=["state", "attempts", "error_code", "error_message", "updated_at"])

        retry, _ = service.prepare_create(self.client, self.system, colombia_profile=self.profile)

        first.refresh_from_db()
        self.assertNotEqual(first.pk, retry.pk)
        self.assertEqual(first.state, AlegraWriteOperation.STATE_FAILED)
        self.assertEqual(first.attempts, 1)
        self.assertEqual(retry.state, AlegraWriteOperation.STATE_PENDING)
        self.assertEqual(retry.result_metadata["supersedes_operation_id"], first.pk)

    def test_create_success_creates_map_atomically(self):
        transport = FakeTransport(create_result={"id": "A-NEW"})
        service = AlegraContactWriteService(transport=transport)
        operation, _ = service.prepare_create(self.client, self.system, colombia_profile=self.profile)
        result = service.execute_create(operation.pk, authorization=self.authorization, colombia_profile=self.profile)
        self.assertEqual(result.state, AlegraWriteOperation.STATE_SYNCED)
        self.assertEqual(ExternalObjectMap.objects.filter(system=self.system, external_id="A-NEW", object_id=self.client.pk).count(), 1)
        self.assertEqual(len(transport.create_calls), 1)

    def test_create_is_blocked_when_remote_candidate_exists(self):
        transport = FakeTransport(candidates=[{"id": "A-EXISTING"}])
        service = AlegraContactWriteService(transport=transport)
        operation, _ = service.prepare_create(self.client, self.system, colombia_profile=self.profile)
        result = service.execute_create(operation.pk, authorization=self.authorization, colombia_profile=self.profile)
        operation.refresh_from_db()
        self.assertEqual(result.state, AlegraWriteOperation.STATE_NEEDS_RECONCILIATION)
        self.assertEqual(operation.state, AlegraWriteOperation.STATE_NEEDS_RECONCILIATION)
        self.assertEqual(operation.attempts, 0)
        self.assertEqual(operation.error_code, "remote_candidate")
        self.assertEqual(transport.create_calls, [])

    def test_create_candidate_check_failure_leaves_operation_pending(self):
        transport = FakeTransport()
        transport.find_candidates = Mock(side_effect=TimeoutError("timeout"))
        service = AlegraContactWriteService(transport=transport)
        operation, _ = service.prepare_create(self.client, self.system, colombia_profile=self.profile)
        with self.assertRaises(TimeoutError):
            service.execute_create(operation.pk, authorization=self.authorization, colombia_profile=self.profile)
        operation.refresh_from_db()
        self.assertEqual(operation.state, AlegraWriteOperation.STATE_PENDING)
        self.assertEqual(operation.attempts, 0)
        self.assertEqual(transport.create_calls, [])

    @patch.dict("os.environ", {"ALEGRA_EMAIL": "test@example.test", "ALEGRA_API_TOKEN": "secret"}, clear=False)
    @patch("tienda.services.alegra_write.AlegraReadOnlyClient")
    def test_candidate_search_reads_all_pages_and_filters_identity(self, readonly_class):
        readonly_class.return_value.paged_get.return_value = [
            AlegraResponse(200, {"data": [{"id": "A-1", "identificationObject": {"type": "CC", "number": "123456789"}}]}, "https://example.test/contacts"),
            AlegraResponse(200, {"data": [{"id": "A-2", "identificationObject": {"type": "CC", "number": "999999999"}}]}, "https://example.test/contacts"),
        ]
        transport = AlegraWriteClient()
        rows = transport.find_candidates(identification="123.456.789")
        self.assertEqual([row["id"] for row in rows], ["A-1"])
        readonly_class.return_value.paged_get.assert_called_once()
        self.assertIsNone(readonly_class.return_value.paged_get.call_args.kwargs["limit"])

    @patch.dict("os.environ", {"ALEGRA_EMAIL": "test@example.test", "ALEGRA_API_TOKEN": "secret"}, clear=False)
    @patch("tienda.services.alegra_write.AlegraReadOnlyClient")
    def test_candidate_search_propagates_incomplete_pagination(self, readonly_class):
        readonly_class.return_value.paged_get.side_effect = AlegraPaginationError("página repetida")
        with self.assertRaises(AlegraPaginationError):
            AlegraWriteClient().find_candidates(identification="123456789")

    def test_uncertain_create_blocks_retry(self):
        transport = FakeTransport(create_error=AlegraWriteUncertain("timeout"))
        service = AlegraContactWriteService(transport=transport)
        operation, _ = service.prepare_create(self.client, self.system, colombia_profile=self.profile)
        result = service.execute_create(operation.pk, authorization=self.authorization, colombia_profile=self.profile)
        self.assertEqual(result.state, AlegraWriteOperation.STATE_NEEDS_RECONCILIATION)
        with self.assertRaises(WriteConflict):
            service.execute_create(operation.pk, authorization=self.authorization, colombia_profile=self.profile)
        self.assertEqual(len(transport.create_calls), 1)

    def test_sent_create_blocks_second_worker_without_second_post(self):
        transport = FakeTransport()
        service = AlegraContactWriteService(transport=transport)
        operation, _ = service.prepare_create(self.client, self.system, colombia_profile=self.profile)
        operation.state = AlegraWriteOperation.STATE_SENT
        operation.attempts = 1
        operation.save(update_fields=["state", "attempts"])
        with self.assertRaises(WriteConflict):
            service.execute_create(operation.pk, authorization=self.authorization, colombia_profile=self.profile)
        self.assertEqual(len(transport.create_calls), 0)

    def test_success_without_external_id_requires_reconciliation(self):
        service = AlegraContactWriteService(transport=FakeTransport(create_result={}))
        operation, _ = service.prepare_create(self.client, self.system, colombia_profile=self.profile)
        result = service.execute_create(operation.pk, authorization=self.authorization, colombia_profile=self.profile)
        self.assertEqual(result.state, AlegraWriteOperation.STATE_NEEDS_RECONCILIATION)

    def test_update_uses_put_scope_and_get_confirmation(self):
        ct = ContentType.objects.get_for_model(Cliente)
        ExternalObjectMap.objects.create(system=self.system, resource_type="contacts", external_id="A-1", content_type=ct, object_id=self.client.pk)
        remote = {
            "id": "A-1", "name": "Cliente local", "identification": "900123456",
            "identificationObject": {"type": "NIT", "number": "900123456", "dv": "1"},
            "kindOfPerson": "LEGAL_ENTITY", "regime": "COMMON_REGIME", "type": ["client"],
            "status": "active", "email": "old@example.test", "phonePrimary": "3000000000",
        }
        baseline = {"nombre": "Cliente local", "identificacion": "900123456", "email": "old@example.test", "telefono": "3000000000"}
        self.client.email = "new@example.test"
        self.client.save(update_fields=["email"])
        transport = FakeTransport(detail={"id": "A-1", "name": "Cliente local"})
        service = AlegraContactWriteService(transport=transport)
        operation, payload = service.prepare_update(self.client, self.system, external_id="A-1", remote_row=remote, baseline=baseline)
        self.assertEqual(payload, {
            "identification": "900123456",
            "identificationObject": {"type": "NIT", "number": "900123456", "dv": "1"},
            "kindOfPerson": "LEGAL_ENTITY", "regime": "COMMON_REGIME", "type": "client", "status": "active",
            "name": "Cliente Local SAS", "email": "new@example.test",
        })
        update_auth = authorize_external_write(client_id=self.client.pk, operation="PUT", environment="production", confirmed=True)
        result = service.execute_update(operation.pk, authorization=update_auth)
        self.assertEqual(result.state, AlegraWriteOperation.STATE_SYNCED)
        self.assertEqual(transport.update_calls[0][1], payload)

    def test_update_conflict_and_empty_remote_are_blocked(self):
        ct = ContentType.objects.get_for_model(Cliente)
        ExternalObjectMap.objects.create(system=self.system, resource_type="contacts", external_id="A-1", content_type=ct, object_id=self.client.pk)
        service = AlegraContactWriteService(transport=FakeTransport())
        with self.assertRaises(WriteConflict):
            service.prepare_update(self.client, self.system, external_id="A-1",
                                   remote_row={"id": "A-1", "name": "Otro"},
                                   baseline={"nombre": "Anterior"})
        with self.assertRaises(ValidationError):
            service.prepare_update(self.client, self.system, external_id="A-1",
                                   remote_row={"id": "A-1", "name": "Cliente local", "email": ""},
                                   baseline={"nombre": "Cliente local", "email": ""})

    def test_update_uses_colombia_name_object_and_excludes_protected_fields(self):
        natural = Cliente.objects.create(
            nombre="Persona", primer_nombre="Ana", segundo_nombre="María",
            primer_apellido="Pérez", segundo_apellido="Gómez",
            tipo_cliente=Cliente.TIPO_PERSONA, tipo_identificacion=Cliente.ID_CC,
            identificacion="1019059655", regimen_tributario="SIMPLIFIED_REGIME",
        )
        context = {
            "identification": "1019059655",
            "identificationObject": {"type": "CC", "number": "1019059655"},
            "kindOfPerson": "PERSON_ENTITY", "regime": "SIMPLIFIED_REGIME",
            "type": "client", "status": "active",
        }
        payload = AlegraContactWriteService._build_update_payload(
            natural, ["nombre", "identificacion", "regimen_tributario"], external_context=context,
        )
        self.assertEqual(payload["name"], "Ana María Pérez Gómez")
        self.assertEqual(payload["nameObject"], {
            "firstName": "Ana", "secondName": "María", "lastName": "Pérez", "secondLastName": "Gómez",
        })
        self.assertEqual(payload["identification"], "1019059655")
        self.assertEqual(payload["identificationObject"]["type"], "CC")
        self.assertEqual(payload["regime"], "SIMPLIFIED_REGIME")

    def test_update_context_is_required_and_preserved_from_remote(self):
        natural = Cliente.objects.create(
            nombre="Persona", primer_nombre="Ana", primer_apellido="Pérez",
            tipo_cliente=Cliente.TIPO_PERSONA, identificacion="1019059655",
            tipo_identificacion=Cliente.ID_CC, regimen_tributario="COMMON_REGIME",
        )
        with self.assertRaises(ValidationError):
            AlegraContactWriteService._build_update_payload(natural, ["nombre"])
        context = AlegraContactWriteService._build_update_context({
            "identification": "1019059655",
            "identificationObject": {"type": "CC", "number": "1019059655"},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME",
            "type": ["client"], "status": "active",
        })
        self.assertEqual(context["type"], "client")
        self.assertEqual(context["identificationObject"]["type"], "CC")

    def test_update_blocks_missing_colombian_context(self):
        with self.assertRaises(ValidationError):
            AlegraContactWriteService._build_update_context({
                "id": "A-1", "name": "Cliente local", "status": "active",
            })

    def test_update_http_errors_are_audited_without_retry(self):
        ct = ContentType.objects.get_for_model(Cliente)
        ExternalObjectMap.objects.create(system=self.system, resource_type="contacts", external_id="A-1", content_type=ct, object_id=self.client.pk)
        self.client.email = "new@example.test"
        self.client.save(update_fields=["email"])
        for index, status in enumerate((400, 401, 403, 409, 500), start=1):
            with self.subTest(status=status):
                transport = FakeTransport(update_error=AlegraWriteHTTPError(status, f"HTTP {status}"))
                self.client.email = f"new{index}@example.test"
                self.client.save(update_fields=["email"])
                service = AlegraContactWriteService(transport=transport)
                operation, _ = service.prepare_update(
                    self.client, self.system, external_id="A-1",
                    remote_row={
                        "id": "A-1", "name": "Cliente Local SAS", "email": "old@example.test",
                        "identification": "900123456",
                        "identificationObject": {"type": "NIT", "number": "900123456", "dv": "1"},
                        "kindOfPerson": "LEGAL_ENTITY", "regime": "COMMON_REGIME",
                        "type": ["client"], "status": "active",
                    },
                    baseline={"nombre": "Cliente Local SAS", "email": "old@example.test"},
                )
                result = service.execute_update(operation.pk, authorization=self.authorization)
                self.assertEqual(result.state, AlegraWriteOperation.STATE_FAILED)
                self.assertEqual(result.attempts, 1)
                self.assertEqual(len(transport.update_calls), 1)

    def test_update_timeout_requires_reconciliation(self):
        ct = ContentType.objects.get_for_model(Cliente)
        ExternalObjectMap.objects.create(system=self.system, resource_type="contacts", external_id="A-1", content_type=ct, object_id=self.client.pk)
        self.client.email = "new@example.test"
        self.client.save(update_fields=["email"])
        transport = FakeTransport(update_error=AlegraWriteUncertain("timeout"))
        service = AlegraContactWriteService(transport=transport)
        operation, _ = service.prepare_update(
            self.client, self.system, external_id="A-1",
            remote_row={
                "id": "A-1", "name": "Cliente Local SAS", "email": "old@example.test",
                "identification": "900123456",
                "identificationObject": {"type": "NIT", "number": "900123456", "dv": "1"},
                "kindOfPerson": "LEGAL_ENTITY", "regime": "COMMON_REGIME",
                "type": ["client"], "status": "active",
            },
            baseline={"nombre": "Cliente Local SAS", "email": "old@example.test"},
        )
        result = service.execute_update(operation.pk, authorization=self.authorization)
        self.assertEqual(result.state, AlegraWriteOperation.STATE_NEEDS_RECONCILIATION)

    @patch("tienda.management.commands.alegra_escritura_cliente.AlegraWriteClient")
    def test_single_post_command_blocks_without_colombia_profile(self, client_class):
        client_class.return_value.find_candidates.return_value = []
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command("alegra_escritura_cliente", client_id=self.client.pk, stdout=output)
        self.assertEqual(AlegraWriteOperation.objects.count(), 0)

    @patch("tienda.management.commands.alegra_escritura_cliente.AlegraWriteClient")
    def test_single_post_requires_exact_confirmation(self, client_class):
        client_class.return_value.find_candidates.return_value = []
        with self.assertRaises(CommandError):
            call_command("alegra_escritura_cliente", client_id=self.client.pk, execute=True, confirm="incorrecto")
        self.assertEqual(AlegraWriteOperation.objects.count(), 0)

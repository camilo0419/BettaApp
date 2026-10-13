from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core import signing
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from tienda.models import AlegraWriteOperation, Cliente, ExternalObjectMap, ExternalSystem
from tienda.services.database_lock import advisory_lock
from tienda.services.alegra_write import build_contact_payload


class ClienteCrearViewTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="staff-create-client", password="Test123!", is_staff=True)
        self.client.force_login(self.staff)
        self.url = reverse("panel_cliente_crear")

    @patch.dict(
        "os.environ",
        {
            "ALEGRA_EXTERNAL_WRITES_ENABLED": "true",
            "ALEGRA_AUTOMATION_WRITES_ENABLED": "false",
            "ALEGRA_API_TOKEN": "token-no-debe-aparecer",
        },
        clear=False,
    )
    def test_estado_banderas_usa_el_proceso_web_y_no_expone_secretos(self):
        self.staff.is_superuser = True
        self.staff.save(update_fields=["is_superuser"])
        response = self.client.get(reverse("alegra_writes_configuration_status"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "external_writes_enabled": True,
                "automation_writes_enabled": False,
            },
        )
        body = response.content.decode()
        self.assertNotIn("ALEGRA_API_TOKEN", body)
        self.assertNotIn("token-no-debe-aparecer", body)

    def test_estado_banderas_requiere_permisos_de_alegra(self):
        self.staff.user_permissions.clear()
        response = self.client.get(reverse("alegra_writes_configuration_status"))

        self.assertEqual(response.status_code, 403)

    @patch("tienda.views.AlegraWriteClient")
    def test_revision_muestra_decisiones_para_conflicto_de_identificacion(self, transport_class):
        cliente = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Cliente identidad",
            primer_nombre="Ana", primer_apellido="Prueba",
            tipo_identificacion=Cliente.ID_CC, identificacion="900000002",
            regimen_tributario="COMMON_REGIME",
        )
        system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        ExternalObjectMap.objects.create(
            system=system, resource_type="contacts", external_id="identity-view",
            content_type=ContentType.objects.get_for_model(Cliente), object_id=cliente.pk,
            status=ExternalObjectMap.STATUS_ACTIVE,
            metadata={
                "source": "alegra_write",
                "last_synced_fields": {
                    "nombre": cliente.nombre, "tipo_identificacion": "cc",
                    "identificacion": "900000001",
                },
            },
        )
        transport_class.return_value.get_contact.return_value = {
            "id": "identity-view", "name": cliente.nombre,
            "identificationObject": {"type": "CC", "number": "900000001"},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME",
            "type": ["client"], "status": "active",
        }
        response = self.client.get(reverse("panel_cliente_alegra_revisar_cambios", args=[cliente.pk]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Conservar identificación de Alegra", body)
        self.assertIn("Aplicar identificación de BettaApp", body)
        self.assertIn("900000002", body)
        self.assertIn("900000001", body)

    def test_post_invalido_muestra_errores_y_no_crea_cliente(self):
        response = self.client.post(self.url, {"tipo_cliente": Cliente.TIPO_PERSONA, "nombre": ""})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Cliente.objects.count(), 0)
        self.assertContains(response, "No se pudo guardar el cliente")
        self.assertContains(response, "El nombre completo es obligatorio para una persona.")

    def test_post_valido_crea_un_cliente_y_no_consulta_alegra_en_el_guardado(self):
        data = {
            "tipo_cliente": Cliente.TIPO_PERSONA,
            "nombre": "Cliente Formulario QA",
            "primer_nombre": "Cliente",
            "primer_apellido": "Formulario",
            "tipo_identificacion": Cliente.ID_CC,
            "identificacion": "1019059653",
            "regimen_tributario": "SIMPLIFIED_REGIME",
            "direccion": "Calle 10 # 20-30",
            "activo": "on",
        }
        with patch("tienda.views.AlegraWriteClient") as transport, \
             patch("tienda.services.alegra_operation_queue.AlegraWriteClient") as queue_transport:
            response = self.client.post(self.url, data)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertEqual(Cliente.objects.get().nombre, "Cliente Formulario")
        transport.assert_not_called()
        operation = AlegraWriteOperation.objects.get()
        self.assertEqual(operation.state, AlegraWriteOperation.STATE_PENDING)
        queue_transport.return_value.find_candidates.assert_not_called()

    def test_post_valido_deja_candidato_para_revision_del_procesador(self):
        data = {
            "tipo_cliente": Cliente.TIPO_PERSONA,
            "nombre": "Cliente Duplicado QA",
            "primer_nombre": "Cliente",
            "primer_apellido": "Duplicado",
            "tipo_identificacion": Cliente.ID_CC,
            "identificacion": "1019059655",
            "regimen_tributario": "SIMPLIFIED_REGIME",
            "direccion": "Calle 10 # 20-30",
            "activo": "on",
        }
        with patch("tienda.services.alegra_operation_queue.AlegraWriteClient") as queue_transport:
            response = self.client.post(self.url, data)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Cliente.objects.count(), 1)
        from tienda.models import AlegraWriteOperation
        operation = AlegraWriteOperation.objects.get()
        self.assertEqual(operation.state, AlegraWriteOperation.STATE_PENDING)
        queue_transport.return_value.find_candidates.assert_not_called()

    def test_nombres_separados_construyen_nombre_local(self):
        data = {
            "tipo_cliente": Cliente.TIPO_PERSONA,
            "nombre": "Persona",
            "primer_nombre": "Ana",
            "segundo_nombre": "María",
            "primer_apellido": "Pérez",
            "segundo_apellido": "Gómez",
            "tipo_identificacion": Cliente.ID_CC,
            "identificacion": "1019059654",
            "regimen_tributario": "SIMPLIFIED_REGIME",
            "direccion": "Calle 10 # 20-30",
            "activo": "on",
        }
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        cliente = Cliente.objects.get()
        self.assertEqual(cliente.nombre, "Ana María Pérez Gómez")
        self.assertEqual(cliente.nombre_mostrado, "Ana María Pérez Gómez")
        self.assertEqual(build_contact_payload(cliente)["nameObject"]["firstName"], "Ana")

    def test_editar_cliente_sin_vinculo_prepara_alta_automatica(self):
        cliente = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_EMPRESA,
            nombre="Veltrixco",
            razon_social="Veltrixco S.A.S.",
            identificacion="902079339",
            tipo_identificacion=Cliente.ID_NIT,
            regimen_tributario="COMMON_REGIME",
            direccion="Calle 10 # 20-30",
        )
        data = {
            "tipo_cliente": Cliente.TIPO_EMPRESA,
            "nombre": "Veltrixco",
            "razon_social": "Veltrixco S.A.S.",
            "identificacion": "902079339",
            "tipo_identificacion": Cliente.ID_NIT,
            "regimen_tributario": "COMMON_REGIME",
            "direccion": "Calle 10 # 20-30",
            "activo": "on",
        }
        with patch("tienda.services.alegra_operation_queue.AlegraWriteClient") as queue_transport:
            queue_transport.return_value.find_candidates.return_value = []
            response = self.client.post(reverse("panel_cliente_editar", args=[cliente.pk]), data)

        self.assertEqual(response.status_code, 302)
        operation = AlegraWriteOperation.objects.get(client=cliente)
        self.assertEqual(operation.operation, AlegraWriteOperation.OP_CREATE)
        self.assertEqual(operation.state, AlegraWriteOperation.STATE_PENDING)
        queue_transport.return_value.find_candidates.assert_not_called()

        response = self.client.post(reverse("panel_cliente_editar", args=[cliente.pk]), data)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(AlegraWriteOperation.objects.filter(client=cliente, operation=AlegraWriteOperation.OP_CREATE).count(), 1)

    def test_editar_cliente_historico_sin_vinculo_no_encola_alta(self):
        cliente = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_EMPRESA,
            nombre="Histórico",
            razon_social="Histórico S.A.S.",
            alegra_sync_policy=Cliente.ALEGRA_SYNC_HISTORICAL_PENDING,
            direccion="Calle 10 # 20-30",
        )
        data = {
            "tipo_cliente": Cliente.TIPO_EMPRESA,
            "nombre": "Histórico",
            "razon_social": "Histórico S.A.S.",
            "direccion": "Calle 10 # 20-30",
            "activo": "on",
        }

        response = self.client.post(reverse("panel_cliente_editar", args=[cliente.pk]), data)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(AlegraWriteOperation.objects.filter(client=cliente).exists())

    def test_detalle_muestra_estado_persistente_de_sincronizacion(self):
        cliente = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_EMPRESA,
            nombre="Estado QA",
            razon_social="Estado QA S.A.S.",
            identificacion="900123456",
            tipo_identificacion=Cliente.ID_NIT,
            regimen_tributario="COMMON_REGIME",
        )
        response = self.client.get(reverse("panel_cliente_detalle", args=[cliente.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pendiente")
        self.assertContains(response, "Aún no existe una confirmación externa.")

    def test_detalle_cierra_modal_y_deja_estado_discreto_al_confirmar_synced(self):
        cliente = Cliente.objects.create(nombre="Modal confirmado")
        system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        operation = AlegraWriteOperation.objects.create(
            system=system, client=cliente, operation=AlegraWriteOperation.OP_UPDATE,
            idempotency_key="modal-confirmed-key", state=AlegraWriteOperation.STATE_SYNCED,
            attempts=1,
        )
        response = self.client.get(
            f"{reverse('panel_cliente_detalle', args=[cliente.pk])}?sync_operation={operation.pk}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data.state === "synced"', html=False)
        self.assertContains(response, "modal.hidden = true", html=False)

    def test_estado_de_operacion_no_expone_datos_y_exige_cliente_correcto(self):
        cliente = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_EMPRESA,
            nombre="Estado operación",
            razon_social="Estado operación S.A.S.",
            identificacion="900123457",
            tipo_identificacion=Cliente.ID_NIT,
            regimen_tributario="COMMON_REGIME",
        )
        other = Cliente.objects.create(nombre="Otro cliente")
        system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        operation = AlegraWriteOperation.objects.create(
            system=system,
            client=cliente,
            operation=AlegraWriteOperation.OP_CREATE,
            idempotency_key="status-operation-key",
            state=AlegraWriteOperation.STATE_PENDING,
        )
        response = self.client.get(reverse("panel_cliente_alegra_operacion_estado", args=[cliente.pk, operation.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "pending")
        self.assertNotIn("Estado operación", response.content.decode())
        forbidden = self.client.get(reverse("panel_cliente_alegra_operacion_estado", args=[other.pk, operation.pk]))
        self.assertEqual(forbidden.status_code, 404)

    def test_estado_de_conciliacion_identificacion_es_contextual_y_no_crea_operaciones(self):
        cliente = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Identidad pendiente",
            primer_nombre="Identidad", primer_apellido="Pendiente",
            identificacion="444444444", tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME",
        )
        system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        operation = AlegraWriteOperation.objects.create(
            system=system, client=cliente, operation=AlegraWriteOperation.OP_UPDATE,
            idempotency_key="identity-pending-status", state=AlegraWriteOperation.STATE_PENDING,
            result_metadata={"resolution_kind": "identification", "authorized_conflict_fields": ["tipo_identificacion", "identificacion"]},
        )
        status = self.client.get(reverse("panel_cliente_alegra_operacion_estado", args=[cliente.pk, operation.pk]))
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["state"], "pending")
        self.assertEqual(status.json()["resolution_kind"], "identification")
        self.assertIsNone(status.json()["review_url"])

        operation.state = AlegraWriteOperation.STATE_NEEDS_RECONCILIATION
        operation.error_code = "identity_duplicate"
        operation.reconciliation_reason = "La identificación ya pertenece a otro contacto."
        operation.save(update_fields=["state", "error_code", "reconciliation_reason", "updated_at"])
        status = self.client.get(reverse("panel_cliente_alegra_operacion_estado", args=[cliente.pk, operation.pk]))
        self.assertEqual(status.json()["state"], "reconciliation")
        self.assertEqual(status.json()["resolution_kind"], "identification")
        self.assertIn("revisar-cambios", status.json()["review_url"])
        self.assertEqual(AlegraWriteOperation.objects.filter(client=cliente).count(), 1)

    def test_candidato_remoto_ofrece_revision_explicita_en_detalle_y_estado(self):
        cliente = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Cliente revisión", primer_nombre="Cliente",
            primer_apellido="Revisión", identificacion="1017166214", tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME",
        )
        system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        operation = AlegraWriteOperation.objects.create(
            system=system, client=cliente, operation=AlegraWriteOperation.OP_CREATE,
            idempotency_key="candidate-status-key", state=AlegraWriteOperation.STATE_NEEDS_RECONCILIATION,
            error_code="remote_candidate", result_metadata={"remote_candidates": [{
                "id": "678", "name": "Contacto remoto", "identification": "1017166214",
                "identification_type": "CC", "kind_of_person": "PERSON_ENTITY",
                "regime": "COMMON_REGIME", "status": "active",
            }]},
        )
        detail = self.client.get(reverse("panel_cliente_detalle", args=[cliente.pk]))
        self.assertContains(detail, "Revisar coincidencia")
        modal_detail = self.client.get(
            f"{reverse('panel_cliente_detalle', args=[cliente.pk])}?sync_operation={operation.pk}"
        )
        self.assertContains(modal_detail, "Conciliación con Alegra")
        self.assertContains(modal_detail, "Se encontró un contacto en Alegra con la misma identificación")
        self.assertContains(modal_detail, "data-operation-review")
        status = self.client.get(reverse("panel_cliente_alegra_operacion_estado", args=[cliente.pk, operation.pk]))
        self.assertEqual(status.json()["candidate_count"], 1)
        self.assertIn("revisar-candidato", status.json()["review_url"])
        review = self.client.get(reverse("panel_cliente_alegra_revisar_candidato", args=[cliente.pk, operation.pk]))
        self.assertContains(review, "Confirmar vinculación")
        self.assertContains(review, "Contacto remoto")

    def test_candidato_ya_vinculado_no_ofrece_confirmacion_inevitable(self):
        cliente = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Cliente pendiente", primer_nombre="Cliente",
            primer_apellido="Pendiente", identificacion="1017166215", tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME",
        )
        owner = Cliente.objects.create(nombre="Cliente propietario")
        system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        operation = AlegraWriteOperation.objects.create(
            system=system, client=cliente, operation=AlegraWriteOperation.OP_CREATE,
            idempotency_key="candidate-already-linked", state=AlegraWriteOperation.STATE_NEEDS_RECONCILIATION,
            error_code="remote_candidate", result_metadata={"remote_candidates": [{
                "id": "676", "name": "Contacto remoto", "identification": "1017166215",
                "identification_type": "CC", "kind_of_person": "PERSON_ENTITY",
                "regime": "COMMON_REGIME", "status": "active",
            }]},
        )
        from django.contrib.contenttypes.models import ContentType
        ExternalObjectMap.objects.create(
            system=system, resource_type="contacts", external_id="676",
            content_type=ContentType.objects.get_for_model(Cliente), object_id=owner.pk,
            status=ExternalObjectMap.STATUS_ACTIVE,
        )

        detail = self.client.get(reverse("panel_cliente_detalle", args=[cliente.pk]))
        self.assertContains(detail, "Vínculo previo detectado")
        self.assertContains(detail, "Revisar conflicto de vínculo")
        review = self.client.get(reverse("panel_cliente_alegra_revisar_candidato", args=[cliente.pk, operation.pk]))
        self.assertContains(review, "Vínculo existente")
        self.assertNotContains(review, "Confirmar vinculación")

    @patch("tienda.views.AlegraWriteClient")
    def test_revision_historica_sin_candidatos_guardados_consulta_y_renderiza(self, transport_class):
        cliente = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Cliente histórico", primer_nombre="Cliente",
            primer_apellido="Histórico", identificacion="1017166214", tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME",
        )
        system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        operation = AlegraWriteOperation.objects.create(
            system=system, client=cliente, operation=AlegraWriteOperation.OP_CREATE,
            idempotency_key="historical-candidate-key", state=AlegraWriteOperation.STATE_NEEDS_RECONCILIATION,
            error_code="remote_candidate", result_metadata={},
        )
        transport_class.return_value.find_candidates.return_value = [{
            "id": "678", "name": "Contacto histórico",
            "identificationObject": {"type": "CC", "number": "1017166214"},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME", "status": "active",
        }]
        response = self.client.get(reverse("panel_cliente_alegra_revisar_candidato", args=[cliente.pk, operation.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Contacto histórico")
        operation.refresh_from_db()
        self.assertEqual(operation.result_metadata["candidate_count"], 1)

    @patch("tienda.views.AlegraWriteClient")
    def test_revision_historica_descarta_metadata_de_candidatos_malformada(self, transport_class):
        cliente = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Cliente legado", primer_nombre="Cliente",
            primer_apellido="Legado", identificacion="1017166215", tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME",
        )
        system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        operation = AlegraWriteOperation.objects.create(
            system=system, client=cliente, operation=AlegraWriteOperation.OP_CREATE,
            idempotency_key="malformed-candidate-key", state=AlegraWriteOperation.STATE_NEEDS_RECONCILIATION,
            error_code="remote_candidate", result_metadata={"remote_candidates": [1, "678"]},
        )
        transport_class.return_value.find_candidates.return_value = [{
            "id": "678", "name": "Contacto recuperado",
            "identificationObject": {"type": "CC", "number": "1017166215"},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME", "status": "active",
        }]
        response = self.client.get(reverse("panel_cliente_alegra_revisar_candidato", args=[cliente.pk, operation.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Contacto recuperado")

    @patch("tienda.services.alegra_write.AlegraWriteClient")
    def test_post_confirmacion_vincula_candidato_sin_http_500(self, transport_class):
        cliente = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Cliente 164", primer_nombre="Cliente",
            primer_apellido="Prueba", identificacion="1017166214", tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME",
        )
        system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        candidate = {
            "id": "676", "name": "Nombre remoto distinto", "identification": "1017166214",
            "identification_type": "CC", "kind_of_person": "PERSON_ENTITY",
            "regime": "COMMON_REGIME", "status": "active", "dv": "", "type": "",
        }
        operation = AlegraWriteOperation.objects.create(
            system=system, client=cliente, operation=AlegraWriteOperation.OP_CREATE,
            idempotency_key="confirm-candidate-164", state=AlegraWriteOperation.STATE_NEEDS_RECONCILIATION,
            error_code="remote_candidate", result_metadata={"remote_candidates": [candidate]},
        )
        transport_class.return_value.get_contact.return_value = {
            "id": "676", "name": "Nombre remoto distinto", "identification": "1017166214",
            "identificationObject": {"type": "CC", "number": "1017166214"},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME", "status": "active",
        }
        token = signing.dumps(
            {"client_id": cliente.pk, "operation_id": operation.pk, "candidate_ids": ["676"]},
            salt="tienda.alegra_candidate_review",
        )
        response = self.client.post(
            reverse("panel_cliente_alegra_vincular_candidato", args=[cliente.pk, operation.pk]),
            {"candidate_token": token, "external_id": "676"},
        )
        self.assertEqual(response.status_code, 302)
        operation.refresh_from_db()
        self.assertEqual(
            operation.state,
            AlegraWriteOperation.STATE_SYNCED,
            [str(message) for message in get_messages(response.wsgi_request)],
        )
        repeated = self.client.post(
            reverse("panel_cliente_alegra_vincular_candidato", args=[cliente.pk, operation.pk]),
            {"candidate_token": token, "external_id": "676"},
        )
        self.assertEqual(repeated.status_code, 302)
        self.assertEqual(
            ExternalObjectMap.objects.filter(system=system, resource_type="contacts", external_id="676").count(),
            1,
        )

    @patch("tienda.views.process_pending_operations")
    def test_endpoint_procesa_unicamente_la_operacion_del_cliente(self, process):
        cliente = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_EMPRESA,
            nombre="Procesamiento acotado",
            razon_social="Procesamiento acotado S.A.S.",
            identificacion="900123458",
            tipo_identificacion=Cliente.ID_NIT,
            regimen_tributario="COMMON_REGIME",
        )
        system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        operation = AlegraWriteOperation.objects.create(
            system=system, client=cliente, operation=AlegraWriteOperation.OP_CREATE,
            idempotency_key="process-operation-key", state=AlegraWriteOperation.STATE_PENDING,
        )
        process.return_value = {"results": [{"id": operation.pk, "state": "synced"}]}
        response = self.client.post(reverse("panel_cliente_alegra_operacion_procesar", args=[cliente.pk, operation.pk]))
        self.assertEqual(response.status_code, 200)
        process.assert_called_once_with(
            limit=1, timeout=15, execute=True, actor=self.staff,
            operation_id=operation.pk,
        )

    @patch("tienda.views.process_pending_operations")
    def test_endpoint_devuelve_confirmacion_real_si_termina_sincronizada(self, process):
        cliente = Cliente.objects.create(nombre="Confirmación visual")
        system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        operation = AlegraWriteOperation.objects.create(
            system=system, client=cliente, operation=AlegraWriteOperation.OP_UPDATE,
            idempotency_key="confirmed-status-key", state=AlegraWriteOperation.STATE_PENDING,
        )

        def complete_operation(**kwargs):
            operation.state = AlegraWriteOperation.STATE_SYNCED
            operation.attempts = 1
            operation.save(update_fields=["state", "attempts", "updated_at"])
            return {"results": [{"id": operation.pk, "state": AlegraWriteOperation.STATE_SYNCED}]}

        process.side_effect = complete_operation
        response = self.client.post(
            reverse("panel_cliente_alegra_operacion_procesar", args=[cliente.pk, operation.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], AlegraWriteOperation.STATE_SYNCED)
        self.assertEqual(response.json()["message"], "Alegra confirmó la sincronización del cliente.")

    @patch.dict("os.environ", {"ALEGRA_AUTOMATION_WRITES_ENABLED": "false"}, clear=False)
    def test_endpoint_informa_configuracion_sin_dejar_operacion_en_error(self):
        cliente = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_EMPRESA,
            nombre="Procesamiento bloqueado",
            identificacion="900123459",
            tipo_identificacion=Cliente.ID_NIT,
            regimen_tributario="COMMON_REGIME",
        )
        system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        operation = AlegraWriteOperation.objects.create(
            system=system, client=cliente, operation=AlegraWriteOperation.OP_CREATE,
            idempotency_key="configuration-operation-key", state=AlegraWriteOperation.STATE_PENDING,
        )
        response = self.client.post(reverse("panel_cliente_alegra_operacion_procesar", args=[cliente.pk, operation.pk]))

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.json()["state"], AlegraWriteOperation.STATE_PENDING)
        self.assertEqual(operation.attempts, 0)

    def test_endpoint_no_reprocesa_operacion_terminal(self):
        cliente = Cliente.objects.create(nombre="Ya sincronizado")
        system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        operation = AlegraWriteOperation.objects.create(
            system=system, client=cliente, operation=AlegraWriteOperation.OP_CREATE,
            idempotency_key="already-synced-key", state=AlegraWriteOperation.STATE_SYNCED,
        )
        with patch("tienda.views.process_pending_operations") as process:
            response = self.client.post(reverse("panel_cliente_alegra_operacion_procesar", args=[cliente.pk, operation.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["started"])
        process.assert_not_called()

    @patch("tienda.views.process_pending_operations")
    def test_endpoint_reporta_pending_si_el_bloqueo_global_esta_ocupado(self, process):
        cliente = Cliente.objects.create(nombre="Bloqueo concurrente")
        system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        operation = AlegraWriteOperation.objects.create(
            system=system, client=cliente, operation=AlegraWriteOperation.OP_UPDATE,
            idempotency_key="busy-lock-operation-key", state=AlegraWriteOperation.STATE_PENDING,
        )
        with advisory_lock("bettaapp:alegra:outbound:contacts"):
            response = self.client.post(
                reverse("panel_cliente_alegra_operacion_procesar", args=[cliente.pk, operation.pk])
            )

        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["error_code"], "processing_in_progress")
        self.assertEqual(response.json()["state"], AlegraWriteOperation.STATE_PENDING)
        self.assertFalse(response.json()["started"])
        process.assert_not_called()

    def test_endpoint_exige_csrf(self):
        cliente = Cliente.objects.create(nombre="CSRF")
        system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        operation = AlegraWriteOperation.objects.create(
            system=system, client=cliente, operation=AlegraWriteOperation.OP_CREATE,
            idempotency_key="csrf-operation-key", state=AlegraWriteOperation.STATE_PENDING,
        )
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        response = csrf_client.post(reverse("panel_cliente_alegra_operacion_procesar", args=[cliente.pk, operation.pk]))
        self.assertEqual(response.status_code, 403)

    def test_crear_cliente_auto_con_datos_fiscales_incompletos_persiste_bloqueo(self):
        data = {
            "tipo_cliente": Cliente.TIPO_PERSONA,
            "nombre": "Cliente incompleto",
            "primer_nombre": "Cliente",
            "primer_apellido": "Incompleto",
            "tipo_identificacion": Cliente.ID_CC,
            "identificacion": "1017166214",
            "direccion": "Calle 10 # 20-30",
            "activo": "on",
        }
        response = self.client.post(self.url, data)

        self.assertEqual(response.status_code, 302)
        operation = AlegraWriteOperation.objects.get(client__identificacion="1017166214")
        self.assertEqual(operation.operation, AlegraWriteOperation.OP_CREATE)
        self.assertEqual(operation.state, AlegraWriteOperation.STATE_NEEDS_RECONCILIATION)
        self.assertEqual(operation.error_code, "payload_validation")

    def test_editar_cliente_auto_corrige_datos_y_prepara_alta_pendiente(self):
        cliente = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA,
            nombre="Cliente incompleto",
            primer_nombre="Cliente",
            primer_apellido="Incompleto",
            tipo_identificacion=Cliente.ID_CC,
            identificacion="111111111",
        )
        self.client.post(
            reverse("panel_cliente_editar", args=[cliente.pk]),
            {
                "tipo_cliente": Cliente.TIPO_PERSONA,
                "nombre": "Cliente incompleto",
                "primer_nombre": "Cliente",
                "primer_apellido": "Incompleto",
                "tipo_identificacion": Cliente.ID_CC,
                "identificacion": "111111111",
                "direccion": "Calle 10 # 20-30",
                "activo": "on",
            },
        )
        self.assertEqual(
            AlegraWriteOperation.objects.filter(client=cliente, state=AlegraWriteOperation.STATE_NEEDS_RECONCILIATION).count(),
            1,
        )

        with patch("tienda.services.alegra_operation_queue.AlegraWriteClient") as queue_transport:
            queue_transport.return_value.find_candidates.return_value = []
            response = self.client.post(
                reverse("panel_cliente_editar", args=[cliente.pk]),
                {
                    "tipo_cliente": Cliente.TIPO_PERSONA,
                    "nombre": "Cliente incompleto",
                    "primer_nombre": "Cliente",
                    "primer_apellido": "Incompleto",
                    "tipo_identificacion": Cliente.ID_CC,
                    "identificacion": "111111111",
                    "regimen_tributario": "COMMON_REGIME",
                    "direccion": "Calle 10 # 20-30",
                    "activo": "on",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            AlegraWriteOperation.objects.filter(client=cliente, state=AlegraWriteOperation.STATE_PENDING).count(),
            1,
        )

    def test_save_directo_no_permite_nombre_generico_con_nombres_completos(self):
        cliente = Cliente(
            tipo_cliente=Cliente.TIPO_PERSONA,
            nombre="Persona",
            primer_nombre="Laura",
            segundo_nombre="Sofía",
            primer_apellido="Gómez",
            segundo_apellido="Rojas",
        )
        cliente.save()
        cliente.refresh_from_db()
        self.assertEqual(cliente.nombre, "Laura Sofía Gómez Rojas")

    def test_save_historico_sin_nombres_separados_conserva_nombre(self):
        cliente = Cliente(tipo_cliente=Cliente.TIPO_PERSONA, nombre="Nombre histórico")
        cliente.save()
        cliente.refresh_from_db()
        self.assertEqual(cliente.nombre, "Nombre histórico")

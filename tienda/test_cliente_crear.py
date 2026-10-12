from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from tienda.models import AlegraWriteOperation, Cliente, ExternalSystem
from tienda.services.alegra_write import build_contact_payload


class ClienteCrearViewTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="staff-create-client", password="Test123!", is_staff=True)
        self.client.force_login(self.staff)
        self.url = reverse("panel_cliente_crear")

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

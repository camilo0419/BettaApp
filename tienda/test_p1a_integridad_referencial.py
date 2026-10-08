from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from tienda.models import (
    AlegraInvoiceStaging,
    CarteraGestion,
    Categoria,
    Cliente,
    ClientePuntoVenta,
    CompromisoPago,
    Cotizacion,
    ExternalObjectMap,
    Producto,
    Proyecto,
    Solicitud,
    Venta,
    VentaFacturaAlegra,
)
from tienda.services.integrity import link_invoice_to_sale, set_project_points_of_sale


class P1AReferentialIntegrityTests(TestCase):
    def setUp(self):
        self.system = ExternalObjectMap._meta.get_field("system").remote_field.model.objects.create(code="alegra", name="Alegra")
        self.client_a = Cliente.objects.create(nombre="Cliente A", identificacion="9001234567")
        self.client_b = Cliente.objects.create(nombre="Cliente B", identificacion="8001234561")
        self.category = Categoria.objects.create(nombre="P1A")
        self.product = Producto.objects.create(nombre="Producto P1A", categoria=self.category)
        self.user = get_user_model().objects.create_superuser(username="p1a-admin", password="pass")
        self.point_a = ClientePuntoVenta.objects.create(cliente=self.client_a, nombre="Sede A")
        self.point_b = ClientePuntoVenta.objects.create(cliente=self.client_b, nombre="Sede B")

    def invoice(self, external_id="INV-P1A", *, client=None, identification="9001234567"):
        return AlegraInvoiceStaging.objects.create(
            system=self.system,
            external_id=external_id,
            number=external_id,
            client_identification=identification,
            matched_client=client,
            total=Decimal("100"),
            balance=Decimal("100"),
            external_status=AlegraInvoiceStaging.STATUS_OPEN,
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 2, 1),
            fetched_at=timezone.now(),
        )

    def test_invoice_sale_different_clients_is_rejected_by_service_and_model(self):
        invoice = self.invoice(client=self.client_a)
        sale = Venta.objects.create(cliente=self.client_b)
        with self.assertRaises(ValidationError):
            link_invoice_to_sale(invoice.pk, sale.pk, evidence="Orden contradictoria", actor=self.user)
        with self.assertRaises(ValidationError):
            VentaFacturaAlegra.objects.create(venta=sale, factura=invoice, evidencia="Prueba", confirmado_por=self.user)
        self.assertEqual(VentaFacturaAlegra.objects.count(), 0)

    def test_unmatched_invoice_requires_authorized_manual_review(self):
        invoice = self.invoice("INV-UNMATCHED", client=None, identification="")
        sale = Venta.objects.create(cliente=self.client_a)
        regular = get_user_model().objects.create_user(username="p1a-regular", password="pass", is_staff=True)
        with self.assertRaises(PermissionDenied):
            link_invoice_to_sale(invoice.pk, sale.pk, evidence="Revisión", actor=regular)
        link = link_invoice_to_sale(invoice.pk, sale.pk, evidence="Confirmación documental autorizada", actor=self.user)
        self.assertEqual(link.confirmado_por_id, self.user.id)

    def test_identification_and_external_map_are_used_before_manual_link(self):
        invoice = self.invoice("INV-ID", client=None, identification="900.123.456-7")
        sale = Venta.objects.create(cliente=self.client_a)
        link = link_invoice_to_sale(invoice.pk, sale.pk, evidence="Orden 123", actor=self.user)
        self.assertEqual(link.venta_id, sale.id)

    def test_contradictory_external_mapping_cannot_be_ignored(self):
        invoice = self.invoice("INV-CONTRADICTORY", client=self.client_a, identification="9001234567")
        invoice.client_external_id = "CONTACT-B"
        invoice.save(update_fields=["client_external_id"])
        ExternalObjectMap.objects.create(
            system=self.system,
            resource_type="contacts",
            external_id="CONTACT-B",
            content_type=ContentType.objects.get_for_model(self.client_b),
            object_id=self.client_b.pk,
        )
        sale = Venta.objects.create(cliente=self.client_a)
        with self.assertRaises(ValidationError):
            link_invoice_to_sale(invoice.pk, sale.pk, evidence="Evidencia que contradice la identidad", actor=self.user)

    def test_external_map_rejects_multiple_active_ids_for_one_local_object(self):
        content_type = ContentType.objects.get_for_model(self.product)
        ExternalObjectMap.objects.create(system=self.system, resource_type="items", external_id="ITEM-1", content_type=content_type, object_id=self.product.pk)
        with self.assertRaises((ValidationError, IntegrityError)):
            with transaction.atomic():
                ExternalObjectMap.objects.create(system=self.system, resource_type="items", external_id="ITEM-2", content_type=content_type, object_id=self.product.pk)

    def test_external_map_rejects_duplicate_external_identity(self):
        content_type = ContentType.objects.get_for_model(self.product)
        ExternalObjectMap.objects.create(system=self.system, resource_type="items", external_id="ITEM-1", content_type=content_type, object_id=self.product.pk)
        with self.assertRaises((ValidationError, IntegrityError)):
            with transaction.atomic():
                ExternalObjectMap.objects.create(system=self.system, resource_type="items", external_id="ITEM-1", content_type=content_type, object_id=self.product.pk + 1)

    def test_project_points_service_rejects_other_client_and_model_detects_direct_m2m_write(self):
        project = Proyecto.objects.create(cliente=self.client_a, nombre="Proyecto A")
        with self.assertRaises(ValidationError):
            set_project_points_of_sale(project.pk, [self.point_b.pk])
        project.puntos_venta.add(self.point_b)
        with self.assertRaises(ValidationError):
            project.full_clean()

    def test_commercial_models_reject_cross_client_relations_without_forms(self):
        project_b = Proyecto.objects.create(cliente=self.client_b, nombre="Proyecto B")
        with self.assertRaises(ValidationError):
            Solicitud.objects.create(producto=self.product, cliente=self.client_a, proyecto=project_b, cliente_nombre="A", cliente_celular="300")
        with self.assertRaises(ValidationError):
            Cotizacion.objects.create(cliente=self.client_a, proyecto=project_b, titulo="Cotización inválida")
        with self.assertRaises(ValidationError):
            Venta.objects.create(cliente=self.client_a, proyecto=project_b)

    def test_collection_models_reject_invoice_from_other_client_without_forms(self):
        invoice = self.invoice("INV-B", client=self.client_b, identification="8001234561")
        with self.assertRaises(ValidationError):
            CarteraGestion.objects.create(cliente=self.client_a, factura=invoice, responsable=self.user, creado_por=self.user, tipo="llamada", resultado="Intento")
        with self.assertRaises(ValidationError):
            CompromisoPago.objects.create(cliente=self.client_a, factura=invoice, responsable=self.user, creado_por=self.user, fecha_comprometida=date(2026, 2, 1), valor_comprometido=Decimal("10"))

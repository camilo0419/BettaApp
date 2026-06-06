from decimal import Decimal
from io import BytesIO
import tempfile

from django.conf import settings
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .forms import CotizacionForm, ProyectoForm, validate_image_upload
from .models import (
    Categoria,
    Cliente,
    ClienteContacto,
    ClienteUsuario,
    Cotizacion,
    CotizacionItem,
    EmpleadoPerfil,
    Notificacion,
    NotificacionCliente,
    Producto,
    Proyecto,
    Solicitud,
    SolicitudAsignacion,
    SolicitudNovedad,
    SolicitudTarea,
)
from .services.cotizacion_pdf import generar_pdf_cotizacion, nombre_archivo_cotizacion
from .services.email_service import logo_email_url
from .templatetags.media_extras import safe_media_url


TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


class MediaStorageTests(SimpleTestCase):
    def test_django_52_storages_include_default_and_staticfiles(self):
        self.assertIn("default", settings.STORAGES)
        self.assertIn("staticfiles", settings.STORAGES)
        self.assertEqual(settings.STORAGES["default"]["BACKEND"], "django.core.files.storage.FileSystemStorage")

    def test_safe_media_url_returns_empty_for_missing_local_file(self):
        class DummyFile:
            name = "productos/no-existe.png"

            def __init__(self, storage):
                self.storage = storage

            @property
            def url(self):
                return self.storage.url(self.name)

        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = FileSystemStorage(location=tmp_dir, base_url="/media/")
            self.assertEqual(safe_media_url(DummyFile(storage)), "")

    def test_validate_image_upload_accepts_valid_png(self):
        uploaded = valid_png_upload()
        self.assertIs(validate_image_upload(uploaded), uploaded)
        self.assertEqual(uploaded.tell(), 0)

    def test_validate_image_upload_rejects_invalid_image_content(self):
        uploaded = SimpleUploadedFile("producto.png", b"contenido invalido", content_type="image/png")
        with self.assertRaises(ValidationError):
            validate_image_upload(uploaded)


def valid_png_upload(name="producto.png"):
    image_bytes = BytesIO()
    Image.new("RGB", (1, 1), "#ffffff").save(image_bytes, format="PNG")
    return SimpleUploadedFile(name, image_bytes.getvalue(), content_type="image/png")


@override_settings(STORAGES=TEST_STORAGES)
class PortalClienteTests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(nombre="Categoría Test", activa=True)
        self.producto = Producto.objects.create(
            nombre="Producto Test",
            categoria=self.categoria,
            activo=True,
            destacado=True,
            tipo_calculo=Producto.CALCULO_UNIDAD,
            precio_base_unidad=Decimal("10000"),
        )
        self.cliente = Cliente.objects.create(nombre="Cliente Uno", email="cliente1@example.com", activo=True)
        self.otro_cliente = Cliente.objects.create(nombre="Cliente Dos", email="cliente2@example.com", activo=True)
        self.contacto = ClienteContacto.objects.create(
            cliente=self.cliente,
            nombre="Contacto Uno",
            email="cliente1@example.com",
            telefono="3000000000",
            whatsapp="3000000000",
            es_principal=True,
        )
        self.contacto_dos = ClienteContacto.objects.create(
            cliente=self.cliente,
            nombre="Contacto Dos",
            email="contacto2@example.com",
            telefono="3000000002",
            whatsapp="3000000002",
        )
        self.user = User.objects.create_user(
            username="cliente1@example.com",
            email="cliente1@example.com",
            password="PortalTest123!",
            is_staff=False,
        )
        self.cliente_usuario = ClienteUsuario.objects.create(
            cliente=self.cliente,
            user=self.user,
            contacto=self.contacto,
            puede_ver_facturacion=True,
        )
        self.proyecto = Proyecto.objects.create(nombre="Proyecto Cliente", cliente=self.cliente, contacto=self.contacto, estado=Proyecto.ESTADO_PRODUCCION)
        self.solicitud = Solicitud.objects.create(
            producto=self.producto,
            cliente=self.cliente,
            contacto=self.contacto,
            proyecto=self.proyecto,
            cliente_nombre="Cliente Uno",
            cliente_celular="3000000000",
            cliente_email="cliente1@example.com",
            precio_estimado=Decimal("10000"),
            valor_facturado=Decimal("12000"),
            estado_facturacion=Solicitud.FACT_FACTURADO,
        )
        self.otra_solicitud = Solicitud.objects.create(
            producto=self.producto,
            cliente=self.otro_cliente,
            cliente_nombre="Cliente Dos",
            cliente_celular="3000000001",
            cliente_email="cliente2@example.com",
            precio_estimado=Decimal("5000"),
        )
        SolicitudNovedad.objects.create(
            solicitud=self.solicitud,
            tipo=SolicitudNovedad.TIPO_COMENTARIO,
            comentario="Avance visible",
            visible_para_cliente=True,
        )
        SolicitudNovedad.objects.create(
            solicitud=self.solicitud,
            tipo=SolicitudNovedad.TIPO_COMENTARIO,
            comentario="Comentario interno",
            visible_para_cliente=False,
        )

    def test_panel_producto_editar_uploads_image_without_invalid_storage_error(self):
        staff = User.objects.create_user(username="staff", password="StaffTest123!", is_staff=True)
        self.client.force_login(staff)

        with tempfile.TemporaryDirectory() as tmp_dir, self.settings(MEDIA_ROOT=tmp_dir):
            response = self.client.post(
                reverse("panel_producto_editar", args=[self.producto.id]),
                {
                    "nombre": self.producto.nombre,
                    "slug": self.producto.slug,
                    "categoria": self.categoria.id,
                    "descripcion_corta": self.producto.descripcion_corta,
                    "descripcion_larga": self.producto.descripcion_larga,
                    "imagen_principal": valid_png_upload("producto-panel.png"),
                    "imagen_estatica": self.producto.imagen_estatica,
                    "activo": "on",
                    "destacado": "on",
                    "orden": self.producto.orden,
                    "tipo_calculo": self.producto.tipo_calculo,
                    "precio_base_m2": self.producto.precio_base_m2,
                    "precio_base_unidad": self.producto.precio_base_unidad,
                },
            )

        self.assertEqual(response.status_code, 302)
        self.producto.refresh_from_db()
        self.assertTrue(self.producto.imagen_principal.name.startswith("productos/"))

    def login_cliente(self):
        return self.client.post(
            reverse("cliente_login"),
            {"email": "cliente1@example.com", "password": "PortalTest123!"},
        )

    def test_registro_cliente_crea_user_cliente_y_acceso(self):
        response = self.client.post(
            reverse("cliente_registro"),
            {
                "email": "nuevo@example.com",
                "password1": "PortalNuevo123!",
                "password2": "PortalNuevo123!",
                "tipo_cliente": Cliente.TIPO_PERSONA,
                "nombre": "Nuevo Cliente",
                "tipo_identificacion": Cliente.ID_CC,
                "identificacion": "100200300",
                "telefono": "3010000000",
                "whatsapp": "3010000000",
                "ciudad": "Bogota",
                "direccion": "Calle 1",
                "contacto_principal": "Nuevo Cliente",
                "acepta_terminos": "on",
            },
        )
        self.assertRedirects(response, reverse("cliente_dashboard"))
        self.assertTrue(User.objects.filter(email="nuevo@example.com", is_staff=False).exists())
        self.assertTrue(ClienteUsuario.objects.filter(user__email="nuevo@example.com", activo=True).exists())

    def test_login_email_y_dashboard_propio(self):
        response = self.login_cliente()
        self.assertRedirects(response, reverse("cliente_dashboard"))
        response = self.client.get(reverse("cliente_dashboard"))
        self.assertContains(response, "Cliente Uno")
        self.assertContains(response, "Proyecto Cliente")

    def test_cliente_no_ve_pedido_ajeno(self):
        self.login_cliente()
        response = self.client.get(reverse("cliente_pedido_detalle", args=[self.otra_solicitud.id]))
        self.assertEqual(response.status_code, 404)

    def test_cliente_solo_ve_su_contacto_salvo_permiso_global(self):
        proyecto_dos = Proyecto.objects.create(nombre="Proyecto otro contacto", cliente=self.cliente, contacto=self.contacto_dos)
        solicitud_dos = Solicitud.objects.create(
            producto=self.producto,
            cliente=self.cliente,
            contacto=self.contacto_dos,
            proyecto=proyecto_dos,
            cliente_nombre="Contacto Dos",
            cliente_celular="3000000002",
            cliente_email="contacto2@example.com",
        )
        self.login_cliente()
        response = self.client.get(reverse("cliente_pedidos"))
        self.assertContains(response, "OP-{:06d}".format(self.solicitud.id))
        self.assertNotContains(response, "OP-{:06d}".format(solicitud_dos.id))
        self.assertEqual(self.client.get(reverse("cliente_pedido_detalle", args=[solicitud_dos.id])).status_code, 404)
        self.cliente_usuario.puede_ver_toda_la_cuenta = True
        self.cliente_usuario.save()
        response = self.client.get(reverse("cliente_pedido_detalle", args=[solicitud_dos.id]))
        self.assertEqual(response.status_code, 200)

    def test_facturacion_depende_de_permiso(self):
        self.login_cliente()
        response = self.client.get(reverse("cliente_pedido_detalle", args=[self.solicitud.id]))
        self.assertContains(response, "Información comercial")
        self.assertContains(response, "Facturado")
        self.cliente_usuario.puede_ver_facturacion = False
        self.cliente_usuario.save()
        response = self.client.get(reverse("cliente_pedido_detalle", args=[self.solicitud.id]))
        self.assertNotContains(response, "Información comercial")

    def test_novedades_internas_no_se_exponen(self):
        self.login_cliente()
        response = self.client.get(reverse("cliente_pedido_detalle", args=[self.solicitud.id]))
        self.assertContains(response, "Avance visible")
        self.assertNotContains(response, "Comentario interno")

    def test_cliente_inactivo_no_entra(self):
        self.cliente_usuario.activo = False
        self.cliente_usuario.save()
        response = self.client.post(
            reverse("cliente_login"),
            {"email": "cliente1@example.com", "password": "PortalTest123!"},
        )
        self.assertContains(response, "Tu acceso al portal no esta activo.", status_code=200)

    def test_password_reset_no_revela_email(self):
        response = self.client.post(reverse("cliente_password_reset"), {"email": "nadie@example.com"})
        self.assertRedirects(response, reverse("cliente_password_reset_done"))

    def test_cliente_no_entra_panel_ni_produccion(self):
        self.login_cliente()
        self.assertEqual(self.client.get(reverse("panel_clientes")).status_code, 403)
        self.assertEqual(self.client.get(reverse("produccion_dashboard")).status_code, 403)

    def test_publico_sigue_sin_login(self):
        response = self.client.get(reverse("producto_detalle", args=[self.producto.slug]))
        self.assertEqual(response.status_code, 200)

    def test_cliente_logueado_crea_solicitud_con_su_cuenta(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("producto_detalle", args=[self.producto.slug]),
            {
                "cliente_nombre": "Nombre alterado",
                "cliente_celular": "3999999999",
                "cliente_email": "alterado@example.com",
            },
        )
        solicitud = Solicitud.objects.exclude(pk__in=[self.solicitud.pk, self.otra_solicitud.pk]).latest("id")
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/solicitud/{solicitud.pk}/exito/", response.url)
        self.assertEqual(solicitud.cliente, self.cliente)
        self.assertEqual(solicitud.contacto, self.contacto)
        self.assertEqual(solicitud.cliente_nombre, self.contacto.nombre)
        self.assertEqual(solicitud.cliente_celular, self.contacto.whatsapp)
        self.assertEqual(solicitud.cliente_email, self.contacto.email)

    def test_header_publico_muestra_cuenta_si_cliente_logueado(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("producto_detalle", args=[self.producto.slug]))
        self.assertContains(response, "Mi cuenta")
        self.assertContains(response, self.contacto.nombre)
        self.assertNotContains(response, "Iniciar sesi")

    def test_menu_cliente_incluye_ir_a_la_tienda(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("cliente_dashboard"))
        self.assertContains(response, "Ir a la tienda")
        self.assertContains(response, reverse("productos_catalogo"))
        self.assertContains(response, "data-client-menu-toggle")
        self.assertContains(response, 'aria-expanded="false"')

    def test_email_no_renderiza_logo_roto_sin_url_publica(self):
        html = render_to_string(
            "tienda/emails/cliente_bienvenida.html",
            {
                "cliente_usuario": self.cliente_usuario,
                "cliente": self.cliente,
                "site_url": "https://betta.example.com",
                "from_name": "Betta Diseño",
                "email_logo_url": "",
            },
        )
        self.assertNotIn("<img", html)
        self.assertIn("Betta Diseño", html)

    @override_settings(BETTA_EMAIL_LOGO_URL="https://betta.example.com/static/tienda/img/marca/betta-logo-text.jpeg")
    def test_email_logo_url_solo_acepta_url_absoluta(self):
        self.assertEqual(
            logo_email_url(),
            "https://betta.example.com/static/tienda/img/marca/betta-logo-text.jpeg",
        )

    @override_settings(BETTA_EMAIL_LOGO_URL="/static/tienda/img/marca/betta-logo-text.jpeg")
    def test_email_logo_url_rechaza_ruta_relativa(self):
        self.assertEqual(logo_email_url(), "")

    def test_panel_staff_y_orden_produccion_siguen_funcionando(self):
        staff = User.objects.create_user(username="staff", password="StaffTest123!", is_staff=True)
        self.client.force_login(staff)
        rutas = [
            reverse("panel_dashboard"),
            reverse("panel_clientes"),
            reverse("panel_proyectos"),
            reverse("panel_solicitudes"),
            reverse("panel_solicitud_detalle", args=[self.solicitud.id]),
            reverse("panel_solicitud_orden_produccion", args=[self.solicitud.id]),
        ]
        for ruta in rutas:
            response = self.client.get(ruta)
            self.assertEqual(response.status_code, 200, ruta)

    def test_panel_drawer_inicia_cerrado(self):
        staff = User.objects.create_user(username="staff-drawer", password="StaffTest123!", is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(reverse("panel_dashboard"))
        self.assertContains(response, "data-panel-menu-toggle")
        self.assertContains(response, 'aria-expanded="false"')

    def test_formularios_admin_no_duplican_selector_cliente(self):
        staff = User.objects.create_user(username="staff-forms", password="StaffTest123!", is_staff=True)
        self.client.force_login(staff)
        rutas = [
            reverse("panel_cotizacion_crear"),
            reverse("panel_proyecto_crear"),
            reverse("panel_cotizacion_editar", args=[
                Cotizacion.objects.create(cliente=self.cliente, contacto=self.contacto, proyecto=self.proyecto, solicitud=self.solicitud, titulo="Editar form").id
            ]),
            reverse("panel_proyecto_editar", args=[self.proyecto.id]),
        ]
        for ruta in rutas:
            response = self.client.get(ruta)
            self.assertEqual(response.status_code, 200, ruta)
            html = response.content.decode()
            self.assertEqual(html.count('name="cliente"'), 1, ruta)

    def test_ajax_dependientes_filtran_por_cliente_y_proyecto(self):
        staff = User.objects.create_user(username="staff-ajax", password="StaffTest123!", is_staff=True)
        otro_contacto = ClienteContacto.objects.create(cliente=self.otro_cliente, nombre="Contacto Ajeno", email="ajeno@example.com")
        otro_proyecto = Proyecto.objects.create(nombre="Proyecto Ajeno", cliente=self.otro_cliente, contacto=otro_contacto)
        Solicitud.objects.create(
            producto=self.producto,
            cliente=self.otro_cliente,
            contacto=otro_contacto,
            proyecto=otro_proyecto,
            cliente_nombre="Ajeno",
            cliente_celular="300",
            cliente_email="ajeno@example.com",
        )

        self.client.force_login(staff)
        response = self.client.get(reverse("panel_ajax_cliente_contactos", args=[self.cliente.id]))
        self.assertEqual(response.status_code, 200)
        labels = [item["label"] for item in response.json()["results"]]
        self.assertTrue(any("Contacto Uno" in label for label in labels))
        self.assertFalse(any("Contacto Ajeno" in label for label in labels))

        response = self.client.get(reverse("panel_ajax_cliente_proyectos", args=[self.cliente.id]))
        labels = [item["label"] for item in response.json()["results"]]
        self.assertTrue(any("Proyecto Cliente" in label for label in labels))
        self.assertFalse(any("Proyecto Ajeno" in label for label in labels))

        response = self.client.get(reverse("panel_ajax_cliente_solicitudes", args=[self.cliente.id]))
        ids = [item["id"] for item in response.json()["results"]]
        self.assertIn(self.solicitud.id, ids)
        self.assertNotIn(self.otra_solicitud.id, ids)

        response = self.client.get(reverse("panel_ajax_proyecto_solicitudes", args=[self.proyecto.id]))
        ids = [item["id"] for item in response.json()["results"]]
        self.assertEqual(ids, [self.solicitud.id])

    def test_forms_dependientes_rechazan_relaciones_cruzadas(self):
        otro_contacto = ClienteContacto.objects.create(cliente=self.otro_cliente, nombre="Contacto Ajeno", email="ajeno@example.com")
        otro_proyecto = Proyecto.objects.create(nombre="Proyecto Ajeno", cliente=self.otro_cliente, contacto=otro_contacto)
        otra_solicitud = Solicitud.objects.create(
            producto=self.producto,
            cliente=self.otro_cliente,
            contacto=otro_contacto,
            proyecto=otro_proyecto,
            cliente_nombre="Ajeno",
            cliente_celular="300",
            cliente_email="ajeno@example.com",
        )

        proyecto_form = ProyectoForm(
            data={
                "cliente": self.cliente.id,
                "contacto": otro_contacto.id,
                "nombre": "Proyecto cruzado",
                "estado": Proyecto.ESTADO_BORRADOR,
                "prioridad": Proyecto.PRIORIDAD_NORMAL,
            }
        )
        self.assertFalse(proyecto_form.is_valid())
        self.assertIn("contacto", proyecto_form.errors)

        cotizacion_form = CotizacionForm(
            data={
                "cliente": self.cliente.id,
                "contacto": otro_contacto.id,
                "proyecto": otro_proyecto.id,
                "solicitud": otra_solicitud.id,
                "titulo": "Cotización cruzada",
                "estado": Cotizacion.ESTADO_BORRADOR,
                "moneda": Cotizacion.MONEDA_COP,
                "validez_dias": 15,
                "activa": "on",
            }
        )
        self.assertFalse(cotizacion_form.is_valid())
        self.assertIn("contacto", cotizacion_form.errors)
        self.assertIn("proyecto", cotizacion_form.errors)
        self.assertIn("solicitud", cotizacion_form.errors)

    def test_portal_proyectos_renderiza_cards_profesionales(self):
        self.login_cliente()
        response = self.client.get(reverse("cliente_proyectos"))
        self.assertContains(response, "client-project-card")
        self.assertContains(response, "Ver detalle")

    def test_panel_cliente_detalle_incluye_solicitudes_del_proyecto(self):
        staff = User.objects.create_user(username="staff-proyecto", password="StaffTest123!", is_staff=True)
        solicitud_proyecto = Solicitud.objects.create(
            producto=self.producto,
            proyecto=self.proyecto,
            cliente_nombre="Solicitud por proyecto",
            cliente_celular="3000000002",
            cliente_email="proyecto@example.com",
        )
        self.client.force_login(staff)
        response = self.client.get(reverse("panel_cliente_detalle", args=[self.cliente.id]))
        self.assertContains(response, f"OP-{solicitud_proyecto.id:06d}")

    def test_smoke_rutas_principales_por_rol(self):
        staff = User.objects.create_user(username="staff-smoke", password="StaffTest123!", is_staff=True)
        cotizacion = Cotizacion.objects.create(
            cliente=self.cliente,
            proyecto=self.proyecto,
            solicitud=self.solicitud,
            titulo="Cotización smoke",
            estado=Cotizacion.ESTADO_ENVIADA,
            creada_por=staff,
        )
        CotizacionItem.objects.create(cotizacion=cotizacion, descripcion="ítem smoke", cantidad=Decimal("1"), valor_unitario=Decimal("1000"))

        self.client.force_login(staff)
        rutas_staff = [
            reverse("panel_dashboard"),
            reverse("panel_solicitudes"),
            reverse("panel_clientes"),
            reverse("panel_cliente_detalle", args=[self.cliente.id]),
            reverse("panel_proyectos"),
            reverse("panel_proyecto_detalle", args=[self.proyecto.id]),
            reverse("panel_cotizaciones"),
            reverse("panel_cotizacion_detalle", args=[cotizacion.id]),
            reverse("panel_productos"),
            reverse("panel_producto_campos", args=[self.producto.id]),
            reverse("panel_categorias"),
            reverse("panel_campos_maestros"),
            reverse("panel_empleados"),
            reverse("panel_solicitud_orden_produccion", args=[self.solicitud.id]),
            reverse("panel_cotizacion_pdf", args=[cotizacion.id]),
        ]
        for ruta in rutas_staff:
            response = self.client.get(ruta)
            self.assertEqual(response.status_code, 200, ruta)

        self.client.force_login(self.user)
        rutas_cliente = [
            reverse("cliente_dashboard"),
            reverse("cliente_pedidos"),
            reverse("cliente_pedido_detalle", args=[self.solicitud.id]),
            reverse("cliente_proyectos"),
            reverse("cliente_proyecto_detalle", args=[self.proyecto.id]),
            reverse("cliente_cotizaciones"),
            reverse("cliente_cotizacion_detalle", args=[cotizacion.id]),
            reverse("cliente_perfil"),
            reverse("cliente_notificaciones"),
        ]
        for ruta in rutas_cliente:
            response = self.client.get(ruta)
            self.assertEqual(response.status_code, 200, ruta)

        user = User.objects.create_user(username="prod-smoke", password="ProdTest123!")
        empleado = EmpleadoPerfil.objects.create(user=user, activo=True, puede_recibir_pedidos=True)
        SolicitudAsignacion.objects.create(solicitud=self.solicitud, empleado=empleado)
        tarea = SolicitudTarea.objects.create(solicitud=self.solicitud, titulo="Tarea smoke", responsable=empleado)
        self.client.force_login(user)
        rutas_produccion = [
            reverse("produccion_dashboard"),
            reverse("produccion_pedido_detalle", args=[self.solicitud.id]),
            reverse("produccion_tarea_detalle", args=[tarea.id]),
        ]
        for ruta in rutas_produccion:
            response = self.client.get(ruta)
            self.assertEqual(response.status_code, 200, ruta)

    def test_dashboard_produccion_y_pedido_asignado_funcionan(self):
        user = User.objects.create_user(username="prod", password="ProdTest123!")
        empleado = EmpleadoPerfil.objects.create(user=user, activo=True, puede_recibir_pedidos=True)
        SolicitudAsignacion.objects.create(solicitud=self.solicitud, empleado=empleado)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("produccion_dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("produccion_pedido_detalle", args=[self.solicitud.id])).status_code, 200)

    def test_home_y_catalogo_publico_responden(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("cliente_login"))
        self.assertContains(response, "Iniciar sesi")
        self.assertNotContains(response, reverse("panel_login"))
        self.assertNotContains(response, "Admin productos")
        self.assertEqual(self.client.get(reverse("productos_catalogo")).status_code, 200)

    def test_staff_gestiona_cotizacion_items_totales_y_pdf(self):
        staff = User.objects.create_user(username="staff2", password="StaffTest123!", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("panel_cotizaciones")).status_code, 200)
        response = self.client.post(
            reverse("panel_cotizacion_crear"),
            {
                "cliente": self.cliente.id,
                "proyecto": self.proyecto.id,
                "solicitud": self.solicitud.id,
                "titulo": "Cotización test",
                "descripcion": "Prueba comercial",
                "estado": Cotizacion.ESTADO_BORRADOR,
                "moneda": Cotizacion.MONEDA_COP,
                "validez_dias": 15,
                "activa": "on",
            },
        )
        cotizacion = Cotizacion.objects.get(titulo="Cotización test")
        self.assertRedirects(response, reverse("panel_cotizacion_detalle", args=[cotizacion.id]))
        response = self.client.post(
            reverse("panel_cotizacion_item_crear", args=[cotizacion.id]),
            {
                "producto": self.producto.id,
                "descripcion": "ítem test",
                "detalle": "Detalle",
                "cantidad": "2",
                "unidad": "und",
                "valor_unitario": "10000",
                "descuento_porcentaje": "10",
                "descuento_valor": "1000",
                "impuesto_porcentaje": "19",
                "orden": 1,
                "activo": "on",
            },
        )
        self.assertRedirects(response, reverse("panel_cotizacion_detalle", args=[cotizacion.id]))
        cotizacion.refresh_from_db()
        self.assertEqual(cotizacion.subtotal, Decimal("30000.00"))
        self.assertEqual(cotizacion.descuento_total, Decimal("3000.00"))
        self.assertEqual(cotizacion.impuesto_total, Decimal("3230.00"))
        self.assertEqual(cotizacion.total, Decimal("30230.00"))
        response = self.client.get(reverse("panel_cotizacion_pdf", args=[cotizacion.id]))
        self.assertContains(response, cotizacion.numero)

    def test_permisos_cotizaciones_panel_y_portal_cliente(self):
        cotizacion = Cotizacion.objects.create(
            cliente=self.cliente,
            proyecto=self.proyecto,
            solicitud=self.solicitud,
            titulo="Cotización visible",
            estado=Cotizacion.ESTADO_ENVIADA,
            creada_por=None,
        )
        CotizacionItem.objects.create(
            cotizacion=cotizacion,
            descripcion="ítem visible",
            cantidad=Decimal("1"),
            valor_unitario=Decimal("5000"),
        )
        self.assertEqual(self.client.get(reverse("panel_cotizaciones")).status_code, 302)
        user = User.objects.create_user(username="prod2", password="ProdTest123!")
        EmpleadoPerfil.objects.create(user=user, activo=True, puede_recibir_pedidos=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("panel_cotizaciones")).status_code, 403)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("cliente_cotizaciones")).status_code, 200)
        self.assertEqual(self.client.get(reverse("cliente_cotizacion_detalle", args=[cotizacion.id])).status_code, 200)
        otra = Cotizacion.objects.create(cliente=self.otro_cliente, titulo="Ajena", estado=Cotizacion.ESTADO_ENVIADA)
        self.assertEqual(self.client.get(reverse("cliente_cotizacion_detalle", args=[otra.id])).status_code, 404)

    @override_settings(BETTA_WHATSAPP_NUMBER="57 300-123-4567", BETTA_WHATSAPP_MESSAGE="Hola Betta, necesito asesoria")
    def test_home_favicon_y_whatsapp_configurable(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "tienda/img/favicon/favicon.ico")
        self.assertContains(response, "tienda/img/favicon/site.webmanifest")
        self.assertContains(response, "https://wa.me/573001234567?text=Hola%20Betta%2C%20necesito%20asesoria")
        self.assertContains(response, "No encuentras lo que buscas")

    @override_settings(BETTA_WHATSAPP_NUMBER="")
    def test_home_no_muestra_whatsapp_sin_numero(self):
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, "https://wa.me/")

    def test_bases_principales_incluyen_favicon_y_badges(self):
        staff = User.objects.create_user(username="staff-badge", password="StaffTest123!", is_staff=True)
        user_prod = User.objects.create_user(username="prod-badge", password="ProdTest123!")
        EmpleadoPerfil.objects.create(user=user_prod, activo=True, puede_recibir_pedidos=True)
        Notificacion.objects.create(usuario_destino=user_prod, titulo="Pendiente", mensaje="Tarea asignada")
        NotificacionCliente.objects.create(
            cliente_usuario=self.cliente_usuario,
            cliente=self.cliente,
            titulo="Pedido",
            mensaje="Actualizacion",
        )

        self.client.force_login(staff)
        self.assertContains(self.client.get(reverse("panel_dashboard")), "tienda/img/favicon/favicon.ico")

        self.client.force_login(self.user)
        response = self.client.get(reverse("cliente_dashboard"))
        self.assertContains(response, "tienda/img/favicon/favicon.ico")
        self.assertContains(response, "notification-badge")
        self.assertContains(response, ">1<")

        self.client.force_login(user_prod)
        response = self.client.get(reverse("produccion_dashboard"))
        self.assertContains(response, "tienda/img/favicon/favicon.ico")
        self.assertContains(response, "notification-badge")

    def test_pdf_cotizacion_real_incluye_item_manual(self):
        cotizacion = Cotizacion.objects.create(cliente=self.cliente, titulo="PDF manual")
        CotizacionItem.objects.create(
            cotizacion=cotizacion,
            producto=None,
            descripcion="Item manual completo",
            detalle="Servicio fuera de catalogo",
            cantidad=Decimal("2"),
            unidad="und",
            valor_unitario=Decimal("15000"),
            impuesto_porcentaje=Decimal("19"),
        )
        pdf = generar_pdf_cotizacion(cotizacion)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(nombre_archivo_cotizacion(cotizacion), f"Cotizacion-{cotizacion.numero}-Betta-Diseno.pdf")

    def test_tarea_general_de_proyecto_aparece_en_produccion(self):
        staff = User.objects.create_user(username="staff-task", password="StaffTest123!", is_staff=True)
        user_prod = User.objects.create_user(username="prod-task", password="ProdTest123!")
        empleado = EmpleadoPerfil.objects.create(user=user_prod, activo=True, puede_recibir_pedidos=True)
        self.client.force_login(staff)
        response = self.client.post(
            reverse("panel_proyecto_detalle", args=[self.proyecto.id]),
            {
                "action": "crear_tarea_proyecto",
                "titulo": "Confirmar medidas",
                "descripcion": "Llamar al cliente",
                "responsable": empleado.id,
                "area": SolicitudTarea.AREA_APOYO,
                "estado": SolicitudTarea.ESTADO_PENDIENTE,
                "prioridad": SolicitudTarea.PRIORIDAD_NORMAL,
                "orden": 1,
                "activa": "on",
            },
        )
        self.assertRedirects(response, reverse("panel_proyecto_detalle", args=[self.proyecto.id]))
        tarea = SolicitudTarea.objects.get(titulo="Confirmar medidas")
        self.assertIsNone(tarea.solicitud)
        self.assertEqual(tarea.proyecto, self.proyecto)
        self.assertTrue(Notificacion.objects.filter(usuario_destino=user_prod, tarea=tarea).exists())

        self.client.force_login(user_prod)
        response = self.client.get(reverse("produccion_dashboard"))
        self.assertContains(response, "Confirmar medidas")
        self.assertContains(response, "Tarea general")
        response = self.client.get(reverse("produccion_tarea_detalle", args=[tarea.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "General")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_envio_cotizacion_adjunta_pdf_real(self):
        staff = User.objects.create_user(username="staff-pdf", password="StaffTest123!", is_staff=True)
        self.client.force_login(staff)
        cotizacion = Cotizacion.objects.create(cliente=self.cliente, contacto=self.contacto, titulo="Envio PDF", creada_por=staff)
        CotizacionItem.objects.create(cotizacion=cotizacion, descripcion="Item PDF", cantidad=Decimal("1"), valor_unitario=Decimal("1000"))
        mail.outbox = []
        response = self.client.post(reverse("panel_cotizacion_enviar", args=[cotizacion.id]), {"email": self.contacto.email})
        self.assertRedirects(response, reverse("panel_cotizacion_detalle", args=[cotizacion.id]))
        cotizacion.refresh_from_db()
        self.assertEqual(cotizacion.estado, Cotizacion.ESTADO_ENVIADA)
        self.assertEqual(cotizacion.enviada_a_email, self.contacto.email)
        self.assertEqual(len(mail.outbox), 1)
        adjuntos_pdf = [attachment for attachment in mail.outbox[0].attachments if attachment[2] == "application/pdf"]
        self.assertEqual(len(adjuntos_pdf), 1)
        self.assertTrue(adjuntos_pdf[0][1].startswith(b"%PDF"))

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_envio_cotizacion_cambia_estado_con_backend_consola(self):
        staff = User.objects.create_user(username="staff3", password="StaffTest123!", is_staff=True)
        self.client.force_login(staff)
        cotizacion = Cotizacion.objects.create(cliente=self.cliente, titulo="Envío test", creada_por=staff)
        CotizacionItem.objects.create(cotizacion=cotizacion, descripcion="ítem", cantidad=Decimal("1"), valor_unitario=Decimal("1000"))
        response = self.client.post(reverse("panel_cotizacion_enviar", args=[cotizacion.id]), {"email": "cliente1@example.com"})
        self.assertRedirects(response, reverse("panel_cotizacion_detalle", args=[cotizacion.id]))
        cotizacion.refresh_from_db()
        self.assertEqual(cotizacion.estado, Cotizacion.ESTADO_ENVIADA)
        self.assertEqual(cotizacion.enviada_a_email, "cliente1@example.com")

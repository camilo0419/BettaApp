from datetime import timedelta
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
from django.utils import timezone
from PIL import Image

from .forms import ClienteForm, CotizacionForm, DynamicSolicitudForm, ProyectoForm, validate_image_upload
from .models import (
    Categoria,
    CampoMaestro,
    CampoMaestroOpcion,
    CampoOpcion,
    Cliente,
    ClienteContacto,
    ClientePuntoVenta,
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
    ProductoCampo,
)
from .views import asegurar_opciones_para_campo
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


class ProductoCampoOpcionesTests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(nombre="Categoría campos")
        self.producto = Producto.objects.create(nombre="Producto campos", categoria=self.categoria)

    def crear_campo_con_opciones(self, tipo):
        maestro = CampoMaestro.objects.create(nombre=f"Campo {tipo}", tipo=tipo)
        CampoMaestroOpcion.objects.create(campo_maestro=maestro, etiqueta="Primera opción", valor="primera")
        CampoMaestroOpcion.objects.create(campo_maestro=maestro, etiqueta="Segunda opción", valor="segunda")
        campo = ProductoCampo.desde_maestro(self.producto, maestro)
        campo.save()
        return maestro, campo

    def test_multiselect_copia_todas_las_opciones_maestras_activas(self):
        _maestro, campo = self.crear_campo_con_opciones(ProductoCampo.TIPO_MULTISELECT)
        CampoOpcion.objects.create(campo=campo, etiqueta="Primera opción", valor="primera")

        self.assertEqual(asegurar_opciones_para_campo(campo), 1)
        self.assertCountEqual(campo.opciones.values_list("valor", flat=True), ["primera", "segunda"])

    def test_sincronizacion_no_duplica_opciones_existentes(self):
        _maestro, campo = self.crear_campo_con_opciones(ProductoCampo.TIPO_MULTISELECT)
        asegurar_opciones_para_campo(campo)

        self.assertEqual(asegurar_opciones_para_campo(campo), 0)
        self.assertEqual(campo.opciones.count(), 2)

    def test_select_sigue_copiando_opciones_y_multiselect_las_presenta(self):
        _maestro, campo_select = self.crear_campo_con_opciones(ProductoCampo.TIPO_SELECT)
        asegurar_opciones_para_campo(campo_select)
        self.assertEqual(campo_select.opciones.count(), 2)

        _maestro, campo_multi = self.crear_campo_con_opciones(ProductoCampo.TIPO_MULTISELECT)
        asegurar_opciones_para_campo(campo_multi)
        form = DynamicSolicitudForm(self.producto)
        opciones = dict(form.fields[form.field_name(campo_multi)].choices)
        self.assertEqual(opciones, {str(o.id): o.etiqueta for o in campo_multi.opciones.all()})


class ProduccionDashboardTests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(nombre="Categoría producción")
        self.producto = Producto.objects.create(nombre="Producto producción", categoria=self.categoria)
        self.cliente = Cliente.objects.create(nombre="Cliente producción", activo=True)
        self.solicitud = Solicitud.objects.create(
            producto=self.producto,
            cliente=self.cliente,
            cliente_nombre="Cliente producción",
            cliente_celular="3000000000",
        )
        self.user = User.objects.create_user(username="operario-dashboard", password="Test123!")
        self.empleado = EmpleadoPerfil.objects.create(user=self.user, activo=True, puede_recibir_pedidos=True)
        self.otro_user = User.objects.create_user(username="otro-operario-dashboard", password="Test123!")
        self.otro_empleado = EmpleadoPerfil.objects.create(user=self.otro_user, activo=True, puede_recibir_pedidos=True)

    def crear_tarea(self, titulo, responsable, **kwargs):
        return SolicitudTarea.objects.create(
            solicitud=self.solicitud,
            titulo=titulo,
            responsable=responsable,
            **kwargs,
        )

    def test_operario_ve_solo_tareas_de_las_que_es_responsable(self):
        propia = self.crear_tarea("Tarea propia", self.empleado)
        self.crear_tarea("Tarea de otro", self.otro_empleado)

        self.client.force_login(self.user)
        response = self.client.get(reverse("produccion_dashboard"))

        self.assertContains(response, propia.titulo)
        self.assertNotContains(response, "Tarea de otro")

    def test_tarea_finalizada_no_aparece_por_defecto_y_si_con_filtro_explicito(self):
        tarea = self.crear_tarea("Tarea terminada", self.empleado, estado=SolicitudTarea.ESTADO_TERMINADA)
        self.client.force_login(self.user)

        response = self.client.get(reverse("produccion_dashboard"))
        self.assertNotContains(response, tarea.titulo)

        response = self.client.get(reverse("produccion_dashboard"), {"tarea_estado": SolicitudTarea.ESTADO_TERMINADA})
        self.assertContains(response, tarea.titulo)

    def test_terminada_hoy_cuenta_en_kpi_pero_no_aparece_en_lista_por_defecto(self):
        tarea = self.crear_tarea(
            "Tarea terminada hoy",
            self.empleado,
            estado=SolicitudTarea.ESTADO_TERMINADA,
            fecha_finalizacion=timezone.now(),
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("produccion_dashboard"))

        self.assertEqual(response.context["metricas_tareas"]["terminadas_hoy"], 1)
        self.assertNotContains(response, tarea.titulo)

    def test_tarea_terminada_en_fecha_anterior_no_cuenta_en_kpi(self):
        tarea = self.crear_tarea(
            "Tarea terminada antigua",
            self.empleado,
            estado=SolicitudTarea.ESTADO_TERMINADA,
            fecha_finalizacion=timezone.now() - timedelta(days=1),
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("produccion_dashboard"))

        self.assertEqual(response.context["metricas_tareas"]["terminadas_hoy"], 0)
        self.assertNotContains(response, tarea.titulo)

    def test_tarea_terminada_hoy_fuera_del_alcance_no_cuenta_en_kpi(self):
        tarea = self.crear_tarea(
            "Tarea terminada de otro operario",
            self.otro_empleado,
            estado=SolicitudTarea.ESTADO_TERMINADA,
            fecha_finalizacion=timezone.now(),
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("produccion_dashboard"))

        self.assertEqual(response.context["metricas_tareas"]["terminadas_hoy"], 0)
        self.assertNotContains(response, tarea.titulo)

    def test_administrador_cuenta_tarea_terminada_hoy_en_alcance_global(self):
        tarea = self.crear_tarea(
            "Tarea terminada global hoy",
            self.otro_empleado,
            estado=SolicitudTarea.ESTADO_TERMINADA,
            fecha_finalizacion=timezone.now(),
        )
        admin = User.objects.create_user(username="admin-dashboard-kpi", password="Test123!", is_staff=True)
        self.client.force_login(admin)

        response = self.client.get(reverse("produccion_dashboard"))

        self.assertEqual(response.context["metricas_tareas"]["terminadas_hoy"], 1)
        self.assertNotContains(response, tarea.titulo)

    def test_tarea_vencida_se_identifica_y_aparece_primero(self):
        vencida = self.crear_tarea(
            "Tarea vencida",
            self.empleado,
            fecha_limite=timezone.localdate() - timedelta(days=1),
            prioridad=SolicitudTarea.PRIORIDAD_NORMAL,
        )
        urgente = self.crear_tarea(
            "Tarea urgente",
            self.empleado,
            fecha_limite=timezone.localdate(),
            prioridad=SolicitudTarea.PRIORIDAD_URGENTE,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("produccion_dashboard"))

        self.assertContains(response, "Vencida")
        self.assertLess(response.content.find(vencida.titulo.encode()), response.content.find(urgente.titulo.encode()))

    def test_administrador_conserva_visibilidad_global(self):
        self.crear_tarea("Tarea de otro operario", self.otro_empleado)
        admin = User.objects.create_user(username="admin-dashboard", password="Test123!", is_staff=True)
        self.client.force_login(admin)

        response = self.client.get(reverse("produccion_dashboard"))

        self.assertContains(response, "Tarea de otro operario")


class ProductoOrdenTests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(nombre="Categoría orden")
        self.staff = User.objects.create_user(username="staff-orden", password="Test123!", is_staff=True)
        self.client.force_login(self.staff)

    def datos_producto(self, nombre, orden=""):
        return {
            "nombre": nombre,
            "slug": "",
            "categoria": self.categoria.id,
            "descripcion_corta": "",
            "descripcion_larga": "",
            "imagen_estatica": "",
            "activo": "on",
            "destacado": "on",
            "orden": orden,
            "tipo_calculo": Producto.CALCULO_AREA,
            "precio_base_m2": "0",
            "precio_base_unidad": "0",
            "requiere_revision": "",
        }

    def crear_por_panel(self, nombre, orden=""):
        response = self.client.post(
            reverse("panel_producto_crear"),
            self.datos_producto(nombre, orden),
        )
        self.assertEqual(response.status_code, 302)
        return Producto.objects.get(nombre=nombre)

    def test_primer_producto_recibe_orden_uno(self):
        producto = self.crear_por_panel("Producto uno")

        self.assertEqual(producto.orden, 1)

    def test_nuevo_producto_recibe_el_siguiente_orden_maximo(self):
        Producto.objects.create(nombre="Producto 1", categoria=self.categoria, orden=1)
        Producto.objects.create(nombre="Producto 2", categoria=self.categoria, orden=2)
        Producto.objects.create(nombre="Producto 3", categoria=self.categoria, orden=3)

        producto = self.crear_por_panel("Producto 4")

        self.assertEqual(producto.orden, 4)

    def test_nuevo_producto_no_rellena_huecos(self):
        for orden in [1, 3, 7]:
            Producto.objects.create(nombre=f"Producto {orden}", categoria=self.categoria, orden=orden)

        producto = self.crear_por_panel("Producto siguiente")

        self.assertEqual(producto.orden, 8)

    def test_nuevo_producto_considera_ceros_y_repetidos(self):
        for indice, orden in enumerate([0, 0, 4, 4], start=1):
            Producto.objects.create(nombre=f"Producto existente {indice}", categoria=self.categoria, orden=orden)

        producto = self.crear_por_panel("Producto nuevo")

        self.assertEqual(producto.orden, 5)

    def test_editar_producto_conserva_orden_actual(self):
        producto = Producto.objects.create(nombre="Producto editable", categoria=self.categoria, orden=7)

        response = self.client.post(
            reverse("panel_producto_editar", args=[producto.id]),
            self.datos_producto("Producto editable actualizado", orden=7),
        )

        self.assertEqual(response.status_code, 302)
        producto.refresh_from_db()
        self.assertEqual(producto.orden, 7)


class ProductoCamposDisponibilidadTests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(nombre="Categoría disponibilidad")
        self.producto = Producto.objects.create(nombre="Producto disponibilidad", categoria=self.categoria)
        self.maestro = CampoMaestro.objects.create(
            nombre="Campo maestro disponibilidad",
            tipo=ProductoCampo.TIPO_TEXTO,
        )
        self.campo = ProductoCampo.desde_maestro(self.producto, self.maestro)
        self.campo.save()
        self.staff = User.objects.create_user(username="staff-disponibilidad", password="Test123!", is_staff=True)
        self.client.force_login(self.staff)

    def obtener_campos(self):
        return self.client.get(reverse("panel_producto_campos", args=[self.producto.id]))

    def test_campo_activo_configurado_y_no_disponible(self):
        response = self.obtener_campos()

        self.assertContains(response, self.campo.etiqueta)
        self.assertContains(response, "Campos configurables")
        self.assertNotContains(
            response,
            reverse("panel_campo_asignar_maestro", args=[self.producto.id, self.maestro.id]),
        )

    def test_campo_desactivado_desaparece_de_configurados(self):
        self.campo.activo = False
        self.campo.save()

        response = self.obtener_campos()

        self.assertNotContains(response, f"Maestro: {self.maestro.nombre}")

    def test_campo_desactivado_vuelve_a_maestros_disponibles(self):
        self.campo.activo = False
        self.campo.save()

        response = self.obtener_campos()

        self.assertContains(response, self.maestro.nombre)
        self.assertContains(
            response,
            reverse("panel_campo_asignar_maestro", args=[self.producto.id, self.maestro.id]),
        )

    def test_reagregar_maestro_reactiva_sin_crear_duplicado(self):
        self.campo.activo = False
        self.campo.save()

        response = self.client.post(
            reverse("panel_campo_asignar_maestro", args=[self.producto.id, self.maestro.id]),
        )

        self.assertEqual(response.status_code, 302)
        self.campo.refresh_from_db()
        self.assertTrue(self.campo.activo)
        self.assertEqual(ProductoCampo.objects.filter(producto=self.producto, campo_maestro=self.maestro).count(), 1)


class ProductoCampoPreviewTests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(nombre="Categoría preview")
        self.producto = Producto.objects.create(nombre="Producto preview", categoria=self.categoria)
        self.staff = User.objects.create_user(username="staff-preview", password="Test123!", is_staff=True)
        self.client.force_login(self.staff)

    def obtener_pagina(self):
        return self.client.get(reverse("panel_producto_campos", args=[self.producto.id]))

    def test_maestro_disponible_incluye_datos_de_previsualizacion(self):
        maestro = CampoMaestro.objects.create(
            nombre="Texto preview",
            tipo=ProductoCampo.TIPO_TEXTO,
            ayuda_base="Indicación de ayuda",
            obligatorio_base=True,
        )

        response = self.obtener_pagina()

        self.assertContains(response, "Previsualizar")
        self.assertContains(response, maestro.nombre)
        self.assertContains(response, maestro.ayuda_base)
        self.assertContains(response, "Obligatorio")

    def test_previsualizacion_muestra_solo_opciones_activas(self):
        maestro = CampoMaestro.objects.create(nombre="Selector preview", tipo=ProductoCampo.TIPO_MULTISELECT)
        CampoMaestroOpcion.objects.create(campo_maestro=maestro, etiqueta="Opción activa", valor="activa", activa=True)
        CampoMaestroOpcion.objects.create(campo_maestro=maestro, etiqueta="Opción inactiva", valor="inactiva", activa=False)

        response = self.obtener_pagina()

        self.assertContains(response, "Opción activa")
        self.assertNotContains(response, "Opción inactiva")

    def test_previsualizar_no_crea_ni_modifica_datos(self):
        maestro = CampoMaestro.objects.create(
            nombre="Select sin cambios",
            tipo=ProductoCampo.TIPO_SELECT,
            ayuda_base="Ayuda original",
        )
        CampoMaestroOpcion.objects.create(campo_maestro=maestro, etiqueta="Opción", valor="opcion")
        maestro_actualizado = maestro.actualizado

        response = self.obtener_pagina()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ProductoCampo.objects.filter(producto=self.producto, campo_maestro=maestro).count(), 0)
        self.assertEqual(CampoOpcion.objects.count(), 0)
        maestro.refresh_from_db()
        self.assertEqual(maestro.ayuda_base, "Ayuda original")
        self.assertEqual(maestro.actualizado, maestro_actualizado)


class ProductoCategoriaAjaxTests(TestCase):
    def setUp(self):
        self.url = reverse("panel_categoria_crear_ajax")

    def test_usuario_autorizado_crea_categoria_y_recibe_datos_utilizables(self):
        staff = User.objects.create_user(username="staff-categoria-ajax", password="Test123!", is_staff=True)
        self.client.force_login(staff)

        response = self.client.post(self.url, {"nombre": "Categoría nueva"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["nombre"], "Categoría nueva")
        self.assertTrue(Categoria.objects.filter(pk=payload["id"], nombre="Categoría nueva").exists())

    def test_payload_invalido_devuelve_error_controlado(self):
        staff = User.objects.create_user(username="staff-categoria-invalida", password="Test123!", is_staff=True)
        self.client.force_login(staff)

        response = self.client.post(self.url, {})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(Categoria.objects.count(), 0)

    def test_usuario_sin_permiso_no_puede_crear_categoria(self):
        user = User.objects.create_user(username="usuario-sin-permiso", password="Test123!")
        self.client.force_login(user)

        response = self.client.post(self.url, {"nombre": "No autorizada"})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Categoria.objects.filter(nombre="No autorizada").exists())

    def test_crear_categoria_no_crea_producto_y_formulario_muestra_accion(self):
        staff = User.objects.create_user(username="staff-categoria-form", password="Test123!", is_staff=True)
        self.client.force_login(staff)

        response = self.client.get(reverse("panel_producto_crear"))

        self.assertContains(response, "+ Nueva categoría")
        self.client.post(self.url, {"nombre": "Solo categoría"})
        self.assertEqual(Producto.objects.count(), 0)


class ClienteFormSemanticsTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="staff-cliente-form", password="Test123!", is_staff=True)
        self.client.force_login(self.staff)

    def test_persona_usa_nombre_completo_y_se_guarda(self):
        response = self.client.post(
            reverse("panel_cliente_crear"),
            {"tipo_cliente": Cliente.TIPO_PERSONA, "nombre": "Ana María Rodríguez"},
        )

        self.assertEqual(response.status_code, 302)
        cliente = Cliente.objects.get()
        self.assertEqual(cliente.nombre, "Ana María Rodríguez")
        self.assertContains(self.client.get(reverse("panel_cliente_detalle", args=[cliente.id])), "Ana María Rodríguez")

    def test_empresa_usa_razon_social_como_referencia_interna_si_nombre_vacio(self):
        response = self.client.post(
            reverse("panel_cliente_crear"),
            {
                "tipo_cliente": Cliente.TIPO_EMPRESA,
                "nombre": "",
                "razon_social": "Rodriguez G Inversiones SAS",
                "nombre_comercial": "Los Perritos Los Colores",
            },
        )

        self.assertEqual(response.status_code, 302)
        cliente = Cliente.objects.get()
        self.assertEqual(cliente.nombre, "Rodriguez G Inversiones SAS")
        self.assertEqual(cliente.razon_social, "Rodriguez G Inversiones SAS")
        self.assertEqual(cliente.nombre_comercial, "Los Perritos Los Colores")

    def test_cliente_historico_y_nombre_visible_conservan_compatibilidad(self):
        cliente = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_EMPRESA,
            nombre="Referencia histórica",
            razon_social="Empresa histórica SAS",
            nombre_comercial="Marca histórica",
        )

        response = self.client.get(reverse("panel_cliente_detalle", args=[cliente.id]))

        self.assertContains(response, "Referencia histórica")
        self.assertContains(response, "Empresa histórica SAS")
        self.assertContains(response, "Marca histórica")
        self.assertEqual(str(cliente), "Empresa histórica SAS")

    def test_editar_cliente_no_borra_los_tres_valores(self):
        cliente = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_EMPRESA,
            nombre="Referencia existente",
            razon_social="Razón existente SAS",
            nombre_comercial="Marca existente",
        )

        response = self.client.post(
            reverse("panel_cliente_editar", args=[cliente.id]),
            {
                "tipo_cliente": Cliente.TIPO_EMPRESA,
                "nombre": "Referencia actualizada",
                "razon_social": "Razón actualizada SAS",
                "nombre_comercial": "Marca actualizada",
            },
        )

        self.assertEqual(response.status_code, 302)
        cliente.refresh_from_db()
        self.assertEqual(cliente.nombre, "Referencia actualizada")
        self.assertEqual(cliente.razon_social, "Razón actualizada SAS")
        self.assertEqual(cliente.nombre_comercial, "Marca actualizada")

    def test_empresa_sin_nombres_no_se_guarda(self):
        form = ClienteForm(data={"tipo_cliente": Cliente.TIPO_EMPRESA, "nombre": "", "razon_social": "", "nombre_comercial": ""})

        self.assertFalse(form.is_valid())
        self.assertIn("razon_social", form.errors)


class ClientePuntoVentaTests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(nombre="Categoría puntos")
        self.cliente = Cliente.objects.create(nombre="Cliente puntos")
        self.otro_cliente = Cliente.objects.create(nombre="Otro cliente puntos")
        self.staff = User.objects.create_user(username="staff-puntos", password="Test123!", is_staff=True)
        self.usuario = User.objects.create_user(username="usuario-puntos", password="Test123!")
        self.client.force_login(self.staff)

    def datos_punto(self, nombre, **extra):
        datos = {
            "nombre": nombre,
            "direccion": "Carrera 10 # 20-30",
            "ciudad": "Medellín",
            "contacto": "Contacto sede",
            "telefono": "3000000000",
            "email": "sede@example.com",
            "observaciones": "Observación",
            "activo": "on",
        }
        datos.update(extra)
        return datos

    def test_cliente_puede_tener_multiples_puntos(self):
        for nombre in ["Laureles", "Envigado", "Poblado"]:
            ClientePuntoVenta.objects.create(cliente=self.cliente, nombre=nombre)

        self.assertCountEqual(self.cliente.puntos_venta.values_list("nombre", flat=True), ["Laureles", "Envigado", "Poblado"])

    def test_dos_clientes_pueden_repetir_nombre_de_punto(self):
        ClientePuntoVenta.objects.create(cliente=self.cliente, nombre="Laureles")
        otro = ClientePuntoVenta.objects.create(cliente=self.otro_cliente, nombre="Laureles")

        self.assertEqual(otro.cliente_id, self.otro_cliente.id)
        self.assertEqual(ClientePuntoVenta.objects.filter(nombre="Laureles").count(), 2)

    def test_no_permite_duplicado_dentro_del_mismo_cliente(self):
        ClientePuntoVenta.objects.create(cliente=self.cliente, nombre="Laureles")

        with self.assertRaises(ValidationError):
            ClientePuntoVenta.objects.create(cliente=self.cliente, nombre="laureles")

        response = self.client.post(
            reverse("panel_cliente_punto_venta_crear", args=[self.cliente.id]),
            self.datos_punto("LAURELES"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ClientePuntoVenta.objects.filter(cliente=self.cliente).count(), 1)

    def test_crear_desde_cliente_asigna_relacion_automaticamente(self):
        response = self.client.post(
            reverse("panel_cliente_punto_venta_crear", args=[self.cliente.id]),
            self.datos_punto("Envigado"),
        )

        self.assertEqual(response.status_code, 302)
        punto = ClientePuntoVenta.objects.get(nombre="Envigado")
        self.assertEqual(punto.cliente_id, self.cliente.id)

    def test_editar_no_modifica_cliente_ni_otros_puntos(self):
        punto = ClientePuntoVenta.objects.create(cliente=self.cliente, nombre="Laureles", ciudad="Medellín")
        otro = ClientePuntoVenta.objects.create(cliente=self.cliente, nombre="Envigado", ciudad="Envigado")

        response = self.client.post(
            reverse("panel_cliente_punto_venta_editar", args=[self.cliente.id, punto.id]),
            self.datos_punto("Laureles actualizado", ciudad="Bogotá"),
        )

        self.assertEqual(response.status_code, 302)
        punto.refresh_from_db()
        otro.refresh_from_db()
        self.assertEqual(punto.cliente_id, self.cliente.id)
        self.assertEqual(punto.nombre, "Laureles actualizado")
        self.assertEqual(otro.nombre, "Envigado")
        self.assertEqual(otro.ciudad, "Envigado")

    def test_desactivar_conserva_el_registro(self):
        punto = ClientePuntoVenta.objects.create(cliente=self.cliente, nombre="Laureles")

        response = self.client.post(reverse("panel_cliente_punto_venta_toggle", args=[self.cliente.id, punto.id]))

        self.assertEqual(response.status_code, 302)
        punto.refresh_from_db()
        self.assertFalse(punto.activo)
        self.assertTrue(ClientePuntoVenta.objects.filter(pk=punto.id).exists())

    def test_usuario_sin_acceso_no_puede_administrar_puntos(self):
        self.client.force_login(self.usuario)

        self.assertEqual(self.client.get(reverse("panel_cliente_punto_venta_crear", args=[self.cliente.id])).status_code, 403)
        self.assertEqual(self.client.post(reverse("panel_cliente_punto_venta_crear", args=[self.cliente.id]), self.datos_punto("No autorizado")).status_code, 403)

    def test_no_se_puede_usar_cliente_equivocado_para_editar(self):
        punto = ClientePuntoVenta.objects.create(cliente=self.otro_cliente, nombre="Laureles")

        response = self.client.post(
            reverse("panel_cliente_punto_venta_editar", args=[self.cliente.id, punto.id]),
            self.datos_punto("Cambio indebido"),
        )

        self.assertEqual(response.status_code, 404)
        punto.refresh_from_db()
        self.assertEqual(punto.cliente_id, self.otro_cliente.id)
        self.assertEqual(punto.nombre, "Laureles")

    def test_cliente_sin_puntos_sigue_mostrando_su_detalle(self):
        response = self.client.get(reverse("panel_cliente_detalle", args=[self.cliente.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Puntos de venta")
        self.assertContains(response, "no tiene puntos de venta registrados")


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

from datetime import timedelta
from decimal import Decimal
from functools import wraps
import logging
import re
import unicodedata
from urllib.parse import quote
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import (
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.core import signing
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import BooleanField, Case, Count, IntegerField, Max, Prefetch, Q, Sum, Value, When
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from .forms import (
    CategoriaForm,
    CampoMaestroForm,
    CampoMaestroOpcionForm,
    CampoOpcionForm,
    ClienteContactoForm,
    ClienteForm,
    ClienteLoginForm,
    ClientePasswordResetForm,
    ClientePerfilForm,
    ClientePuntoVentaForm,
    ClienteRegistroForm,
    ClienteUsuarioPortalForm,
    CotizacionEstadoForm,
    CotizacionForm,
    CotizacionItemForm,
    DynamicSolicitudForm,
    AdminProduccionEstadoForm,
    EmpleadoPerfilForm,
    ProyectoForm,
    ProyectoSolicitudForm,
    ProductoCampoForm,
    ProductoForm,
    ProductoImagenForm,
    ProduccionEstadoForm,
    ProduccionTareaEstadoForm,
    SolicitudAsignacionForm,
    SolicitudEstadoForm,
    SolicitudClienteForm,
    SolicitudNovedadForm,
    SolicitudProyectoForm,
    SolicitudTareaForm,
    VentaForm,
    VentaItemForm,
    CarteraGestionForm,
    CompromisoPagoForm,
    CarteraResponsableForm,
    UNSPSCImportJobForm,
)
from .models import (
    Categoria,
    CampoMaestro,
    CampoMaestroOpcion,
    CampoOpcion,
    EmpleadoPerfil,
    Cliente,
    AlegraWriteOperation,
    ClienteContacto,
    ClientePuntoVenta,
    ClienteUsuario,
    Cotizacion,
    CotizacionItem,
    Notificacion,
    NotificacionCliente,
    Producto,
    UNSPSCCode,
    UNSPSCImportJob,
    ProductoCampo,
    ProductoImagen,
    Proyecto,
    Solicitud,
    SolicitudAsignacion,
    SolicitudNovedad,
    SolicitudRespuesta,
    SolicitudTarea,
    AlegraItemStaging,
    AlegraContactStaging,
    ExternalObjectMap,
    ExternalSystem,
    SyncAuditLog,
    Venta,
    VentaItem,
    AlegraInvoiceStaging,
    VentaFacturaAlegra,
    AlegraPaymentStaging,
    CarteraGestion,
    CompromisoPago,
    CarteraResponsable,
)
from .services.cotizacion_pdf import generar_pdf_cotizacion, nombre_archivo_cotizacion
from .services.email_service import enviar_confirmacion_registro_cliente, enviar_confirmacion_solicitud, enviar_correo_cliente, enviar_notificacion_cliente, logo_email_url, remitente_betta
from .services.alegra_import import AlegraItemImporter, AlegraItemReconciler
from .services.alegra_contact_import import AlegraContactImporter, AlegraContactReconciler
from .services.alegra_invoice_import import AlegraInvoiceImporter
from .services.alegra_payment_import import AlegraPaymentImporter
from .services.cartera import cartera_rows, cartera_summary, facturacion_summary, invoice_cartera_row, STATUS_OVERDUE, STATUS_PAID, STATUS_PARTIAL, STATUS_PENDING, STATUS_INSUFFICIENT, AGING_BUCKETS
from .services.centro_control import centro_control_snapshot, refresh_control_alerts
from .services.integrity import link_invoice_to_sale
from .services.sync_freshness import financial_freshness, source_freshness
from .services.alegra_write import AlegraContactWriteService, AlegraError, AlegraWriteClient
from .services.alegra_inbound_sync import InboundClientSyncService, InboundSyncConflict
from .services.alegra_operation_queue import enqueue_create, enqueue_update_if_changed
from .services.alegra_status import alegra_operational_status, check_connection, connection_state
from .services.unspsc import recommend_unspsc, unspsc_result

COTIZACION_TOKEN_SALT = "tienda.cotizacion_exito"
logger = logging.getLogger(__name__)
COMMERCIAL_RESPONSE_WORDS = {
    "precio",
    "precios",
    "subtotal",
    "subtotales",
    "descuento",
    "descuentos",
    "impuesto",
    "impuestos",
    "iva",
    "pago",
    "pagos",
    "cotizacion",
    "cotizaciones",
    "cotizado",
    "cotizada",
}
COMMERCIAL_RESPONSE_PHRASES = [
    "valor unitario",
    "valor total",
    "valor estimado",
    "valor cotizado",
    "valor final",
    "valor de venta",
    "valor venta",
    "valor comercial",
    "total a pagar",
    "total de pago",
    "total cotizacion",
    "total final",
]


def panel_staff_required(view_func):
    @login_required(login_url="panel_login")
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper


def alegra_staff_required(view_func):
    @login_required(login_url="panel_login")
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_staff:
            raise PermissionDenied
        if not request.user.is_superuser and not request.user.has_perm("tienda.view_alegraitemstaging"):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper


def alegra_change_required(view_func):
    @login_required(login_url="panel_login")
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_staff:
            raise PermissionDenied
        if not request.user.is_superuser and not request.user.has_perm("tienda.change_alegraitemstaging"):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper


def alegra_contact_staff_required(view_func):
    @login_required(login_url="panel_login")
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_staff:
            raise PermissionDenied
        if not request.user.is_superuser and not request.user.has_perm("tienda.view_alegracontactstaging"):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapper


def alegra_contact_change_required(view_func):
    @login_required(login_url="panel_login")
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_staff:
            raise PermissionDenied
        if not request.user.is_superuser and not request.user.has_perm("tienda.change_alegracontactstaging"):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapper


def produccion_required(view_func):
    @login_required(login_url="produccion_login")
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_staff:
            return view_func(request, *args, **kwargs)
        perfil = getattr(request.user, "empleado_perfil", None)
        if not request.user.is_active or perfil is None or not perfil.activo:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper


def cliente_portal_required(view_func):
    @login_required(login_url="cliente_login")
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        cliente_usuario = getattr(request.user, "cliente_usuario", None)
        if (
            not request.user.is_active
            or cliente_usuario is None
            or not cliente_usuario.activo
            or not cliente_usuario.cliente.activo
        ):
            if request.user.is_staff:
                return redirect("panel_dashboard")
            if hasattr(request.user, "empleado_perfil"):
                return redirect("produccion_dashboard")
            raise PermissionDenied
        request.cliente_usuario = cliente_usuario
        return view_func(request, *args, **kwargs)

    return wrapper


def cliente_usuario_actual(request):
    return getattr(request, "cliente_usuario", getattr(request.user, "cliente_usuario", None))


def cliente_contacto_principal(cliente):
    if not cliente:
        return None
    return cliente.contactos.filter(activo=True, es_principal=True).first() or cliente.contactos.filter(activo=True).first()


def cliente_usuario_ve_toda_la_cuenta(cliente_usuario):
    return bool(cliente_usuario and cliente_usuario.puede_ver_toda_la_cuenta)


def proyectos_para_cliente_usuario(cliente_usuario, qs=None):
    qs = qs if qs is not None else Proyecto.objects.all()
    qs = qs.filter(cliente=cliente_usuario.cliente)
    if cliente_usuario_ve_toda_la_cuenta(cliente_usuario):
        return qs
    if not cliente_usuario.contacto_id:
        return qs.none()
    return qs.filter(contacto=cliente_usuario.contacto)


def solicitudes_para_cliente_usuario(cliente_usuario, qs=None):
    qs = qs if qs is not None else Solicitud.objects.all()
    qs = qs.filter(Q(cliente=cliente_usuario.cliente) | Q(proyecto__cliente=cliente_usuario.cliente)).distinct()
    if cliente_usuario_ve_toda_la_cuenta(cliente_usuario):
        return qs
    if not cliente_usuario.contacto_id:
        return qs.none()
    return qs.filter(
        Q(contacto=cliente_usuario.contacto)
        | Q(contacto__isnull=True, proyecto__contacto=cliente_usuario.contacto)
    ).distinct()


def cotizaciones_para_cliente_usuario(cliente_usuario, qs=None):
    qs = qs if qs is not None else Cotizacion.objects.all()
    qs = qs.filter(cliente=cliente_usuario.cliente)
    if cliente_usuario_ve_toda_la_cuenta(cliente_usuario):
        return qs
    if not cliente_usuario.contacto_id:
        return qs.none()
    return qs.filter(
        Q(contacto=cliente_usuario.contacto)
        | Q(contacto__isnull=True, proyecto__contacto=cliente_usuario.contacto)
        | Q(contacto__isnull=True, solicitud__contacto=cliente_usuario.contacto)
    ).distinct()


def cliente_usuario_puede_ver_proyecto(cliente_usuario, proyecto):
    if not cliente_usuario or not proyecto:
        return False
    return proyectos_para_cliente_usuario(cliente_usuario, Proyecto.objects.filter(pk=proyecto.pk)).exists()


def cliente_usuario_puede_ver_solicitud(cliente_usuario, solicitud):
    if not cliente_usuario or not solicitud:
        return False
    return solicitudes_para_cliente_usuario(cliente_usuario, Solicitud.objects.filter(pk=solicitud.pk)).exists()


def cliente_usuario_puede_ver_cotizacion(cliente_usuario, cotizacion):
    if not cliente_usuario or not cotizacion:
        return False
    return cotizaciones_para_cliente_usuario(cliente_usuario, Cotizacion.objects.filter(pk=cotizacion.pk)).exists()


def notificacion_cliente_visible(cliente_usuario, notificacion):
    if notificacion.solicitud_id and not cliente_usuario_puede_ver_solicitud(cliente_usuario, notificacion.solicitud):
        return False
    if not notificacion.solicitud_id and notificacion.proyecto_id and not cliente_usuario_puede_ver_proyecto(cliente_usuario, notificacion.proyecto):
        return False
    if notificacion.tipo == NotificacionCliente.TIPO_FACTURACION and not cliente_usuario.puede_ver_facturacion:
        return False
    return True


def solicitud_token(solicitud_id):
    return signing.dumps({"solicitud_id": solicitud_id}, salt=COTIZACION_TOKEN_SALT)


def validate_solicitud_token(pk, token):
    try:
        data = signing.loads(token, salt=COTIZACION_TOKEN_SALT)
    except signing.BadSignature as exc:
        raise Http404 from exc

    if str(data.get("solicitud_id")) != str(pk):
        raise Http404


def safe_panel_redirect(request):
    redirect_to = request.POST.get(REDIRECT_FIELD_NAME) or request.GET.get(REDIRECT_FIELD_NAME)
    if redirect_to and url_has_allowed_host_and_scheme(
        redirect_to,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect_to
    return reverse("panel_dashboard")


def validation_message(error):
    if hasattr(error, "messages"):
        return " ".join(error.messages)
    return str(error)


def normalize_for_matching(value):
    normalized = unicodedata.normalize("NFKD", value or "")
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return normalized.lower().replace("_", " ").replace("-", " ").replace("/", " ")


def respuesta_es_comercial(respuesta):
    pieces = [respuesta.etiqueta, respuesta.tipo]
    if respuesta.campo_id:
        pieces.extend([respuesta.campo.etiqueta, respuesta.campo.nombre_interno])
        if respuesta.campo.campo_maestro_id:
            pieces.extend(
                [
                    respuesta.campo.campo_maestro.nombre,
                    respuesta.campo.campo_maestro.slug,
                    respuesta.campo.campo_maestro.etiqueta_base,
                ]
            )

    normalized_pieces = [normalize_for_matching(piece).strip() for piece in pieces if piece]
    combined = " ".join(normalized_pieces)
    if any(phrase in combined for phrase in COMMERCIAL_RESPONSE_PHRASES):
        return True

    for piece in normalized_pieces:
        tokens = set(piece.split())
        if tokens & COMMERCIAL_RESPONSE_WORDS:
            return True
        if piece in ["valor", "total"]:
            return True
    return False


def respuestas_tecnicas_solicitud(solicitud):
    respuestas = solicitud.respuestas.select_related("campo__campo_maestro").order_by("orden", "id")
    return [respuesta for respuesta in respuestas if not respuesta_es_comercial(respuesta)]


def usuario_puede_ver_solicitud(user, solicitud):
    if not user.is_authenticated or not user.is_active or solicitud is None:
        return False
    if user.is_staff:
        return True
    return (
        SolicitudAsignacion.objects.filter(
            solicitud=solicitud,
            empleado__user=user,
            empleado__activo=True,
            activa=True,
        ).exists()
        or SolicitudTarea.objects.filter(
            solicitud=solicitud,
            responsable__user=user,
            responsable__activo=True,
            activa=True,
        ).exists()
    )


def usuario_puede_ver_tarea(user, tarea):
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_staff:
        return True
    if tarea.responsable_id and tarea.responsable.user_id == user.id and tarea.responsable.activo:
        return True
    if not tarea.solicitud_id:
        return False
    return usuario_puede_ver_solicitud(user, tarea.solicitud)


def usuario_puede_ver_proyecto(user, proyecto):
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_staff:
        return True
    return (
        SolicitudAsignacion.objects.filter(
            solicitud__proyecto=proyecto,
            empleado__user=user,
            empleado__activo=True,
            activa=True,
        ).exists()
        or SolicitudTarea.objects.filter(
            solicitud__proyecto=proyecto,
            responsable__user=user,
            responsable__activo=True,
            activa=True,
        ).exists()
        or SolicitudTarea.objects.filter(
            proyecto=proyecto,
            solicitud__isnull=True,
            responsable__user=user,
            responsable__activo=True,
            activa=True,
        ).exists()
    )


def estado_produccion_label(value):
    return dict(Solicitud.ESTADOS_PRODUCCION).get(value, value or "-")


def estado_tarea_label(value):
    return dict(SolicitudTarea.ESTADOS).get(value, value or "-")


def crear_notificacion(usuario, solicitud, titulo, mensaje, tipo=Notificacion.TIPO_SISTEMA, url_destino="", tarea=None, proyecto=None):
    if not usuario or not usuario.is_active:
        return None
    if not url_destino and tarea:
        url_destino = reverse("produccion_tarea_detalle", args=[tarea.id])
    elif not url_destino and solicitud:
        url_destino = reverse("produccion_pedido_detalle", args=[solicitud.id])
    return Notificacion.objects.create(
        usuario_destino=usuario,
        solicitud=solicitud,
        tarea=tarea,
        proyecto=proyecto,
        titulo=titulo,
        mensaje=mensaje,
        tipo=tipo,
        url_destino=url_destino,
    )


def crear_notificacion_cliente(cliente_usuario, titulo, mensaje, tipo=NotificacionCliente.TIPO_SISTEMA, solicitud=None, proyecto=None, url_destino="", enviar_email=True):
    if (
        not cliente_usuario
        or not cliente_usuario.activo
        or not cliente_usuario.user.is_active
        or not cliente_usuario.cliente.activo
    ):
        return None
    if tipo == NotificacionCliente.TIPO_FACTURACION and not cliente_usuario.puede_ver_facturacion:
        return None
    if solicitud and not cliente_usuario_puede_ver_solicitud(cliente_usuario, solicitud):
        return None
    if proyecto and not solicitud and not cliente_usuario_puede_ver_proyecto(cliente_usuario, proyecto):
        return None
    if not url_destino and solicitud:
        url_destino = reverse("cliente_pedido_detalle", args=[solicitud.id])
    elif not url_destino and proyecto:
        url_destino = reverse("cliente_proyecto_detalle", args=[proyecto.id])

    notificacion = NotificacionCliente.objects.create(
        cliente_usuario=cliente_usuario,
        cliente=cliente_usuario.cliente,
        solicitud=solicitud,
        proyecto=proyecto,
        titulo=titulo,
        mensaje=mensaje[:255],
        tipo=tipo,
        url_destino=url_destino,
    )
    if enviar_email and cliente_usuario.recibe_notificaciones:
        enviar_notificacion_cliente(notificacion)
    return notificacion


def notificar_clientes_solicitud(solicitud, titulo, mensaje, tipo=NotificacionCliente.TIPO_PEDIDO, enviar_email=True):
    cliente = solicitud.cliente
    if cliente is None and solicitud.proyecto_id:
        cliente = solicitud.proyecto.cliente
    if cliente is None:
        return 0
    usuarios = cliente.usuarios_portal.filter(activo=True, user__is_active=True)
    creadas = 0
    for cliente_usuario in usuarios:
        notificacion = crear_notificacion_cliente(
            cliente_usuario,
            titulo,
            mensaje,
            tipo=tipo,
            solicitud=solicitud,
            proyecto=solicitud.proyecto,
            enviar_email=enviar_email,
        )
        if notificacion:
            creadas += 1
    return creadas


def notificar_clientes_proyecto(proyecto, titulo, mensaje, tipo=NotificacionCliente.TIPO_PROYECTO, enviar_email=True):
    if not proyecto.cliente_id:
        return 0
    usuarios = proyecto.cliente.usuarios_portal.filter(activo=True, user__is_active=True)
    creadas = 0
    for cliente_usuario in usuarios:
        notificacion = crear_notificacion_cliente(
            cliente_usuario,
            titulo,
            mensaje,
            tipo=tipo,
            proyecto=proyecto,
            enviar_email=enviar_email,
        )
        if notificacion:
            creadas += 1
    return creadas


def valor_visible_solicitud(solicitud):
    return solicitud.valor_facturado or solicitud.precio_final or solicitud.precio_estimado


def crear_novedad(solicitud, usuario, tipo, comentario, estado_anterior="", estado_nuevo="", archivo_evidencia=None, tarea=None, proyecto=None):
    proyecto = proyecto or getattr(solicitud, "proyecto", None) or getattr(tarea, "proyecto", None)
    return SolicitudNovedad.objects.create(
        solicitud=solicitud,
        proyecto=proyecto,
        tarea=tarea,
        usuario=usuario if getattr(usuario, "is_authenticated", False) else None,
        tipo=tipo,
        comentario=comentario,
        estado_anterior=estado_anterior or "",
        estado_nuevo=estado_nuevo or "",
        archivo_evidencia=archivo_evidencia,
    )


def notificar_asignados(solicitud, titulo, mensaje, tipo=Notificacion.TIPO_NOVEDAD, exclude_user=None):
    asignaciones = solicitud.asignaciones.filter(activa=True, empleado__activo=True, empleado__user__is_active=True).select_related("empleado__user")
    for asignacion in asignaciones:
        if exclude_user and asignacion.empleado.user_id == exclude_user.id:
            continue
        crear_notificacion(asignacion.empleado.user, solicitud, titulo, mensaje, tipo)


def notificar_tarea(tarea, titulo, mensaje, tipo=Notificacion.TIPO_TAREA, exclude_user=None):
    proyecto = tarea.proyecto or getattr(tarea.solicitud, "proyecto", None)
    if tarea.responsable_id and tarea.responsable.user.is_active:
        if not exclude_user or tarea.responsable.user_id != exclude_user.id:
            crear_notificacion(tarea.responsable.user, tarea.solicitud, titulo, mensaje, tipo=tipo, tarea=tarea, proyecto=proyecto)

    if not tarea.solicitud_id:
        return
    asignaciones = tarea.solicitud.asignaciones.filter(
        activa=True,
        empleado__activo=True,
        empleado__user__is_active=True,
    ).select_related("empleado__user")
    for asignacion in asignaciones:
        if exclude_user and asignacion.empleado.user_id == exclude_user.id:
            continue
        if tarea.responsable_id and asignacion.empleado.user_id == tarea.responsable.user_id:
            continue
        crear_notificacion(asignacion.empleado.user, tarea.solicitud, titulo, mensaje, tipo=tipo, tarea=tarea, proyecto=tarea.solicitud.proyecto)


def cambiar_estado_produccion(solicitud, nuevo_estado, usuario, comentario="", notificar=True):
    estado_anterior = solicitud.estado_produccion
    if estado_anterior == nuevo_estado:
        return False
    solicitud.estado_produccion = nuevo_estado
    solicitud.save(update_fields=["estado_produccion", "actualizado"])
    comentario_base = comentario.strip() or f"Estado de producción cambiado de {estado_produccion_label(estado_anterior)} a {estado_produccion_label(nuevo_estado)}."
    crear_novedad(
        solicitud,
        usuario,
        SolicitudNovedad.TIPO_CAMBIO_ESTADO,
        comentario_base,
        estado_anterior=estado_anterior,
        estado_nuevo=nuevo_estado,
    )
    if notificar:
        tipo = Notificacion.TIPO_TERMINADO if nuevo_estado == Solicitud.PROD_TERMINADO else Notificacion.TIPO_ESTADO
        notificar_asignados(
            solicitud,
            f"Solicitud #{solicitud.id}: {estado_produccion_label(nuevo_estado)}",
            comentario_base,
            tipo=tipo,
            exclude_user=usuario if getattr(usuario, "is_authenticated", False) else None,
        )
        notificar_clientes_solicitud(
            solicitud,
            f"Pedido OP-{solicitud.id:06d}: {estado_produccion_label(nuevo_estado)}",
            comentario_base[:240],
            tipo=NotificacionCliente.TIPO_PEDIDO,
        )
    return True


def cambiar_estado_tarea(tarea, nuevo_estado, usuario, comentario="", archivo_evidencia=None, notificar=True):
    estado_anterior = tarea.estado
    if estado_anterior == nuevo_estado and not archivo_evidencia:
        return False
    tarea.estado = nuevo_estado
    update_fields = ["estado", "fecha_actualizacion"]
    if archivo_evidencia:
        tarea.evidencia_archivo = archivo_evidencia
        update_fields.append("evidencia_archivo")
    if nuevo_estado in [SolicitudTarea.ESTADO_TERMINADA, SolicitudTarea.ESTADO_APROBADA]:
        tarea.fecha_finalizacion = timezone.now()
        tarea.finalizada_por = usuario if getattr(usuario, "is_authenticated", False) else None
        update_fields.extend(["fecha_finalizacion", "finalizada_por"])
    tarea.save(update_fields=update_fields)

    tipo = SolicitudNovedad.TIPO_TAREA_FINALIZADA if nuevo_estado == SolicitudTarea.ESTADO_TERMINADA else SolicitudNovedad.TIPO_TAREA_ESTADO
    comentario_base = comentario.strip() or f"Tarea '{tarea.titulo}' cambio de {estado_tarea_label(estado_anterior)} a {estado_tarea_label(nuevo_estado)}."
    crear_novedad(
        tarea.solicitud,
        usuario,
        tipo,
        comentario_base,
        estado_anterior=estado_anterior,
        estado_nuevo=nuevo_estado,
        archivo_evidencia=archivo_evidencia,
        tarea=tarea,
        proyecto=tarea.proyecto,
    )
    if notificar:
        notificar_tarea(
            tarea,
            f"Tarea #{tarea.id}: {estado_tarea_label(nuevo_estado)}",
            comentario_base[:240],
            tipo=Notificacion.TIPO_TAREA,
            exclude_user=usuario if getattr(usuario, "is_authenticated", False) else None,
        )
    return True


def tareas_visibles_para_usuario(user):
    qs = SolicitudTarea.objects.filter(activa=True).select_related(
        "solicitud__producto__categoria",
        "solicitud__proyecto",
        "proyecto",
        "responsable__user",
    )
    if not user.is_staff:
        qs = qs.filter(
            Q(responsable__user=user)
            | Q(solicitud__asignaciones__empleado__user=user, solicitud__asignaciones__activa=True)
        ).distinct()
    return qs


def asegurar_opciones_para_campo(campo, solo_si_sin_opciones=True):
    if (
        campo.campo_maestro_id
        and campo.tipo in [ProductoCampo.TIPO_SELECT, ProductoCampo.TIPO_MULTISELECT]
        and campo.campo_maestro.opciones_maestras.filter(activa=True).exists()
    ):
        opciones_maestras = campo.campo_maestro.opciones_maestras.filter(activa=True)
        valores_maestros = set(opciones_maestras.values_list("valor", flat=True))
        valores_campo = set(campo.opciones.values_list("valor", flat=True))
        if solo_si_sin_opciones and valores_maestros.issubset(valores_campo):
            return 0
        return campo.copiar_opciones_maestras()
    return 0


def home(request):
    productos = (
        Producto.objects.filter(activo=True, destacado=True, categoria__activa=True)
        .select_related("categoria")
        .prefetch_related("imagenes")
        .order_by("orden", "nombre")[:5]
    )
    return render(request, "tienda/home.html", {"productos": productos})


def productos_catalogo(request):
    q = request.GET.get("q", "").strip()
    categoria_slug = request.GET.get("categoria", "").strip()
    categorias = Categoria.objects.filter(activa=True).order_by("orden", "nombre")
    productos_base = (
        Producto.objects.filter(activo=True, categoria__activa=True)
        .select_related("categoria")
        .prefetch_related("imagenes")
        .order_by("categoria__orden", "categoria__nombre", "orden", "nombre")
    )

    categoria_seleccionada = None
    productos = productos_base
    if categoria_slug:
        categoria_seleccionada = categorias.filter(slug=categoria_slug).first()
        productos = productos.filter(categoria=categoria_seleccionada) if categoria_seleccionada else productos.none()
    if q:
        productos = productos.filter(nombre__icontains=q)

    mostrar_resultados = bool(q or categoria_slug)
    categorias_con_productos = []
    if not mostrar_resultados:
        categorias_con_productos = (
            categorias.filter(productos__activo=True)
            .distinct()
            .prefetch_related(
                Prefetch(
                    "productos",
                    queryset=Producto.objects.filter(activo=True, categoria__activa=True)
                    .select_related("categoria")
                    .prefetch_related("imagenes")
                    .order_by("orden", "nombre"),
                )
            )
        )

    return render(
        request,
        "tienda/productos_catalogo.html",
        {
            "categorias": categorias,
            "categorias_con_productos": categorias_con_productos,
            "productos": productos,
            "q": q,
            "categoria_slug": categoria_slug,
            "categoria_seleccionada": categoria_seleccionada,
            "mostrar_resultados": mostrar_resultados,
        },
    )


def producto_detalle(request, slug):
    producto = get_object_or_404(Producto.objects.select_related("categoria"), slug=slug, activo=True, categoria__activa=True)
    for campo in producto.campos_activos.select_related("campo_maestro").prefetch_related("opciones", "campo_maestro__opciones_maestras"):
        asegurar_opciones_para_campo(campo)
    campos = producto.campos_activos.prefetch_related("opciones")
    galeria = producto.imagenes.filter(activa=True)
    cliente_usuario = None
    if request.user.is_authenticated:
        candidato = getattr(request.user, "cliente_usuario", None)
        if candidato and candidato.activo and candidato.cliente.activo:
            cliente_usuario = candidato

    if request.method == "POST":
        form = DynamicSolicitudForm(producto, request.POST, request.FILES, cliente_usuario=cliente_usuario)
        if form.is_valid():
            ctx = form.price_context()
            requiere_revision = producto.requiere_revision or producto.tipo_calculo == Producto.CALCULO_MANUAL
            contacto = form.resolve_portal_contact()
            solicitud = Solicitud.objects.create(
                producto=producto,
                cliente=cliente_usuario.cliente if cliente_usuario else None,
                contacto=contacto,
                cliente_nombre=form.cleaned_data["cliente_nombre"],
                cliente_celular=form.cleaned_data["cliente_celular"],
                cliente_email=form.cleaned_data.get("cliente_email", ""),
                precio_estimado=ctx["total"],
                requiere_revision=requiere_revision,
            )

            for campo in campos:
                name = form.field_name(campo)
                value = form.cleaned_data.get(name)
                archivo = None
                valor_texto = ""
                if campo.tipo in [ProductoCampo.TIPO_ARCHIVO, ProductoCampo.TIPO_IMAGEN]:
                    archivo = request.FILES.get(name)
                    valor_texto = archivo.name if archivo else ""
                elif campo.tipo == ProductoCampo.TIPO_SELECT:
                    try:
                        opcion = CampoOpcion.objects.get(id=value)
                        valor_texto = opcion.etiqueta
                    except Exception:
                        valor_texto = ""
                elif campo.tipo == ProductoCampo.TIPO_MULTISELECT:
                    labels = list(CampoOpcion.objects.filter(id__in=value).values_list("etiqueta", flat=True)) if value else []
                    valor_texto = ", ".join(labels)
                elif campo.tipo == ProductoCampo.TIPO_CHECKBOX:
                    valor_texto = "Sí" if value else "No"
                else:
                    valor_texto = str(value) if value not in [None, ""] else ""

                if valor_texto or archivo:
                    SolicitudRespuesta.objects.create(
                        solicitud=solicitud,
                        campo=campo,
                        etiqueta=campo.etiqueta,
                        tipo=campo.tipo,
                        valor_texto=valor_texto,
                        archivo=archivo,
                        orden=campo.orden,
                    )

            crear_novedad(
                solicitud,
                None,
                SolicitudNovedad.TIPO_SISTEMA,
                "Solicitud creada y pendiente por asignar en producción.",
                estado_nuevo=Solicitud.PROD_PENDIENTE_ASIGNAR,
            )
            if solicitud.cliente_email:
                enviar_confirmacion_solicitud(solicitud, solicitud.cliente_email)
            return redirect("cotizacion_exito", pk=solicitud.pk, token=solicitud_token(solicitud.pk))
    else:
        form = DynamicSolicitudForm(producto, cliente_usuario=cliente_usuario)

    return render(request, "tienda/producto_detalle.html", {"producto": producto, "campos": campos, "galeria": galeria, "form": form})


def cotizacion_exito(request, pk, token):
    validate_solicitud_token(pk, token)
    solicitud = get_object_or_404(Solicitud, pk=pk)
    partes = [
        f"Hola Betta Diseño, quiero cotizar el producto: {solicitud.producto.nombre}",
        f"Solicitud #{solicitud.id}",
        f"Nombre: {solicitud.cliente_nombre}",
        f"Celular: {solicitud.cliente_celular}",
    ]
    if solicitud.precio_estimado and not solicitud.requiere_revision:
        partes.append(f"Precio estimado: ${solicitud.precio_estimado:,.0f}")
    else:
        partes.append("Precio sujeto a revisión del equipo Betta Diseño")
    numero = re.sub(r"\D+", "", getattr(settings, "BETTA_WHATSAPP_NUMBER", "") or "")
    mensaje = quote("\n".join(partes))
    whatsapp_url = f"https://wa.me/{numero}?text={mensaje}" if numero else ""
    return render(request, "tienda/cotizacion_exito.html", {"solicitud": solicitud, "whatsapp_url": whatsapp_url})


def cliente_registro(request):
    if request.user.is_authenticated:
        cliente_usuario = getattr(request.user, "cliente_usuario", None)
        if cliente_usuario and cliente_usuario.activo and cliente_usuario.cliente.activo:
            return redirect("cliente_dashboard")
        if request.user.is_staff:
            return redirect("panel_dashboard")
        if hasattr(request.user, "empleado_perfil"):
            return redirect("produccion_dashboard")

    form = ClienteRegistroForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        cliente_usuario = form.save()
        enviar_confirmacion_registro_cliente(cliente_usuario)
        crear_notificacion_cliente(
            cliente_usuario,
            "Bienvenido al portal",
            "Tu acceso al portal de clientes quedo activo.",
            tipo=NotificacionCliente.TIPO_SISTEMA,
            enviar_email=False,
        )
        login(request, cliente_usuario.user)
        messages.success(request, "Cuenta creada correctamente.")
        return redirect("cliente_dashboard")
    return render(request, "tienda/cliente/registro.html", {"form": form})


def cliente_login(request):
    if request.user.is_authenticated:
        cliente_usuario = getattr(request.user, "cliente_usuario", None)
        if cliente_usuario and cliente_usuario.activo and cliente_usuario.cliente.activo:
            return redirect("cliente_dashboard")
        if request.user.is_staff:
            return redirect("panel_dashboard")
        if hasattr(request.user, "empleado_perfil"):
            return redirect("produccion_dashboard")

    redirect_to = request.POST.get(REDIRECT_FIELD_NAME) or request.GET.get(REDIRECT_FIELD_NAME, "")
    form = ClienteLoginForm(request=request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.user)
        form.cliente_usuario.fecha_ultimo_acceso = timezone.now()
        form.cliente_usuario.save(update_fields=["fecha_ultimo_acceso", "fecha_actualizacion"])
        if redirect_to and url_has_allowed_host_and_scheme(redirect_to, {request.get_host()}, request.is_secure()):
            return redirect(redirect_to)
        return redirect("cliente_dashboard")
    return render(request, "tienda/cliente/login.html", {"form": form, REDIRECT_FIELD_NAME: redirect_to})


@login_required(login_url="cliente_login")
@require_POST
def cliente_logout(request):
    logout(request)
    return redirect("cliente_login")


class ClientePasswordResetView(PasswordResetView):
    form_class = ClientePasswordResetForm
    template_name = "tienda/cliente/password_reset_form.html"
    email_template_name = "tienda/emails/password_reset_cliente.txt"
    html_email_template_name = "tienda/emails/password_reset_cliente.html"
    subject_template_name = "tienda/emails/password_reset_cliente_subject.txt"
    success_url = reverse_lazy("cliente_password_reset_done")

    def form_valid(self, form):
        self.from_email = remitente_betta()
        return super().form_valid(form)

    def get_extra_email_context(self):
        contexto = super().get_extra_email_context() or {}
        contexto.setdefault("site_url", getattr(settings, "SITE_URL", "").rstrip("/"))
        contexto.setdefault("from_name", getattr(settings, "BETTA_EMAIL_FROM_NAME", "Betta Diseño"))
        contexto.setdefault("email_logo_url", logo_email_url())
        return contexto


class ClientePasswordResetDoneView(PasswordResetDoneView):
    template_name = "tienda/cliente/password_reset_done.html"


class ClientePasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "tienda/cliente/password_reset_confirm.html"
    success_url = reverse_lazy("cliente_password_reset_complete")


class ClientePasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "tienda/cliente/password_reset_complete.html"


@cliente_portal_required
def cliente_dashboard(request):
    cliente_usuario = cliente_usuario_actual(request)
    cliente = cliente_usuario.cliente

    solicitudes = solicitudes_para_cliente_usuario(
        cliente_usuario,
        Solicitud.objects.select_related("producto", "proyecto", "contacto"),
    )
    proyectos = proyectos_para_cliente_usuario(
        cliente_usuario,
        Proyecto.objects.select_related("contacto", "responsable__user"),
    ).annotate(num_solicitudes=Count("solicitudes", distinct=True))
    novedades = SolicitudNovedad.objects.filter(
        solicitud__in=solicitudes.values("pk"),
        visible_para_cliente=True,
    ).select_related("solicitud", "usuario")[:6]
    archivos = SolicitudRespuesta.objects.filter(
        solicitud__in=solicitudes.values("pk"),
        visible_para_cliente=True,
        archivo__isnull=False,
    ).exclude(archivo="").select_related("solicitud")[:6]
    notificaciones = [
        notificacion
        for notificacion in cliente_usuario.notificaciones.select_related("solicitud", "proyecto")
        if notificacion_cliente_visible(cliente_usuario, notificacion)
    ][:6]
    cotizaciones = cotizaciones_para_cliente_usuario(
        cliente_usuario,
        Cotizacion.objects.filter(
            estado__in=[
                Cotizacion.ESTADO_ENVIADA,
                Cotizacion.ESTADO_VISTA,
                Cotizacion.ESTADO_APROBADA,
                Cotizacion.ESTADO_RECHAZADA,
                Cotizacion.ESTADO_VENCIDA,
                Cotizacion.ESTADO_CONVERTIDA,
            ],
            activa=True,
        ).select_related("contacto", "proyecto", "solicitud"),
    )

    valor_facturado = 0
    if cliente_usuario.puede_ver_facturacion:
        for solicitud in solicitudes:
            valor_facturado += valor_visible_solicitud(solicitud) or 0

    metricas = {
        "proyectos_activos": proyectos.exclude(estado__in=[Proyecto.ESTADO_TERMINADO, Proyecto.ESTADO_ENTREGADO, Proyecto.ESTADO_CANCELADO]).count(),
        "pedidos_produccion": solicitudes.filter(estado_produccion__in=[Solicitud.PROD_ASIGNADO, Solicitud.PROD_EN_PROCESO, Solicitud.PROD_CON_NOVEDAD, Solicitud.PROD_CALIDAD]).count(),
        "pedidos_terminados": solicitudes.filter(estado_produccion__in=[Solicitud.PROD_TERMINADO, Solicitud.PROD_LISTO_ENTREGA, Solicitud.PROD_ENTREGADO]).count(),
        "valor_facturado": valor_facturado,
        "sin_leer": sum(
            1
            for notificacion in cliente_usuario.notificaciones.filter(leida=False).select_related("solicitud", "proyecto")
            if notificacion_cliente_visible(cliente_usuario, notificacion)
        ),
        "cotizaciones": cotizaciones.count() if cliente_usuario.puede_ver_facturacion else 0,
    }
    return render(
        request,
        "tienda/cliente/dashboard.html",
        {
            "cliente_usuario": cliente_usuario,
            "cliente": cliente,
            "metricas": metricas,
            "solicitudes": solicitudes[:6] if cliente_usuario.puede_ver_solicitudes else [],
            "proyectos": proyectos[:6] if cliente_usuario.puede_ver_proyectos else [],
            "novedades": novedades,
            "archivos": archivos if cliente_usuario.puede_descargar_archivos else [],
            "notificaciones": notificaciones,
            "cotizaciones": cotizaciones[:4] if cliente_usuario.puede_ver_facturacion else [],
        },
    )


@cliente_portal_required
def cliente_pedidos(request):
    cliente_usuario = cliente_usuario_actual(request)
    if not cliente_usuario.puede_ver_solicitudes:
        raise PermissionDenied
    q = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "").strip()
    estado_produccion = request.GET.get("estado_produccion", "").strip()
    proyecto_id = request.GET.get("proyecto", "").strip()
    fecha = request.GET.get("fecha", "").strip()
    solicitudes = solicitudes_para_cliente_usuario(
        cliente_usuario,
        Solicitud.objects.select_related("producto", "proyecto", "contacto"),
    )
    if q:
        filtro = Q(producto__nombre__icontains=q) | Q(proyecto__nombre__icontains=q) | Q(cliente_nombre__icontains=q)
        if q.isdigit():
            filtro |= Q(id=int(q))
        solicitudes = solicitudes.filter(filtro)
    if estado:
        solicitudes = solicitudes.filter(estado=estado)
    if estado_produccion:
        solicitudes = solicitudes.filter(estado_produccion=estado_produccion)
    if proyecto_id.isdigit():
        solicitudes = solicitudes.filter(proyecto_id=proyecto_id)
    if fecha:
        solicitudes = solicitudes.filter(creado__date=fecha)
    proyectos = proyectos_para_cliente_usuario(cliente_usuario, Proyecto.objects.all()).order_by("nombre")
    return render(
        request,
        "tienda/cliente/pedidos.html",
        {
            "cliente_usuario": cliente_usuario,
            "solicitudes": solicitudes,
            "proyectos": proyectos,
            "q": q,
            "estado": estado,
            "estado_produccion": estado_produccion,
            "proyecto_id": proyecto_id,
            "fecha": fecha,
            "estados": Solicitud.ESTADOS,
            "estados_produccion": Solicitud.ESTADOS_PRODUCCION,
        },
    )


@cliente_portal_required
def cliente_pedido_detalle(request, pk):
    cliente_usuario = cliente_usuario_actual(request)
    if not cliente_usuario.puede_ver_solicitudes:
        raise PermissionDenied
    solicitud = get_object_or_404(
        solicitudes_para_cliente_usuario(
            cliente_usuario,
            Solicitud.objects.select_related("producto__categoria", "proyecto", "contacto"),
        ),
        pk=pk,
    )
    respuestas_base = solicitud.respuestas.select_related("campo__campo_maestro").filter(visible_para_cliente=True).order_by("orden", "id")
    respuestas = [
        respuesta
        for respuesta in respuestas_base
        if cliente_usuario.puede_ver_facturacion or not respuesta_es_comercial(respuesta)
    ]
    archivos = [respuesta for respuesta in respuestas if respuesta.archivo and cliente_usuario.puede_descargar_archivos]
    novedades = solicitud.novedades.filter(visible_para_cliente=True).select_related("usuario", "tarea")
    return render(
        request,
        "tienda/cliente/pedido_detalle.html",
        {
            "cliente_usuario": cliente_usuario,
            "solicitud": solicitud,
            "respuestas": respuestas,
            "archivos": archivos,
            "novedades": novedades,
            "valor_visible": valor_visible_solicitud(solicitud) if cliente_usuario.puede_ver_facturacion else None,
        },
    )


@cliente_portal_required
def cliente_proyectos(request):
    cliente_usuario = cliente_usuario_actual(request)
    if not cliente_usuario.puede_ver_proyectos:
        raise PermissionDenied
    q = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "").strip()
    prioridad = request.GET.get("prioridad", "").strip()
    proyectos = proyectos_para_cliente_usuario(
        cliente_usuario,
        Proyecto.objects.select_related("contacto", "responsable__user"),
    )
    if q:
        proyectos = proyectos.filter(Q(nombre__icontains=q) | Q(cliente_nombre__icontains=q) | Q(descripcion__icontains=q))
    if estado:
        proyectos = proyectos.filter(estado=estado)
    if prioridad:
        proyectos = proyectos.filter(prioridad=prioridad)
    proyectos = list(proyectos)
    if cliente_usuario.puede_ver_facturacion:
        for proyecto in proyectos:
            solicitudes_proyecto = solicitudes_para_cliente_usuario(
                cliente_usuario,
                proyecto.solicitudes.select_related("producto", "proyecto"),
            )
            proyecto.valor_visible_total = sum((valor_visible_solicitud(solicitud) or 0) for solicitud in solicitudes_proyecto)
    return render(
        request,
        "tienda/cliente/proyectos.html",
        {
            "cliente_usuario": cliente_usuario,
            "proyectos": proyectos,
            "q": q,
            "estado": estado,
            "prioridad": prioridad,
            "estados": Proyecto.ESTADOS,
            "prioridades": Proyecto.PRIORIDADES,
        },
    )


@cliente_portal_required
def cliente_proyecto_detalle(request, proyecto_id):
    cliente_usuario = cliente_usuario_actual(request)
    if not cliente_usuario.puede_ver_proyectos:
        raise PermissionDenied
    proyecto = get_object_or_404(
        proyectos_para_cliente_usuario(cliente_usuario, Proyecto.objects.select_related("cliente", "contacto", "responsable__user")),
        pk=proyecto_id,
    )
    solicitudes = solicitudes_para_cliente_usuario(cliente_usuario, proyecto.solicitudes.select_related("producto", "contacto")).order_by("-creado")
    novedades = SolicitudNovedad.objects.filter(
        solicitud__in=solicitudes.values("pk"),
        visible_para_cliente=True,
    ).select_related("solicitud", "usuario")[:12]
    archivos = SolicitudRespuesta.objects.filter(
        solicitud__in=solicitudes.values("pk"),
        visible_para_cliente=True,
        archivo__isnull=False,
    ).exclude(archivo="").select_related("solicitud")[:12]
    return render(
        request,
        "tienda/cliente/proyecto_detalle.html",
        {
            "cliente_usuario": cliente_usuario,
            "proyecto": proyecto,
            "solicitudes": solicitudes if cliente_usuario.puede_ver_solicitudes else [],
            "novedades": novedades,
            "archivos": archivos if cliente_usuario.puede_descargar_archivos else [],
            "valor_total": sum((valor_visible_solicitud(s) or 0) for s in solicitudes) if cliente_usuario.puede_ver_facturacion else None,
        },
    )


@cliente_portal_required
def cliente_cotizaciones(request):
    cliente_usuario = cliente_usuario_actual(request)
    if not cliente_usuario.puede_ver_facturacion:
        raise PermissionDenied
    q = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "").strip()
    visibles = [
        Cotizacion.ESTADO_ENVIADA,
        Cotizacion.ESTADO_VISTA,
        Cotizacion.ESTADO_APROBADA,
        Cotizacion.ESTADO_RECHAZADA,
        Cotizacion.ESTADO_VENCIDA,
        Cotizacion.ESTADO_CONVERTIDA,
    ]
    cotizaciones = cotizaciones_para_cliente_usuario(
        cliente_usuario,
        Cotizacion.objects.filter(estado__in=visibles, activa=True).select_related("contacto", "proyecto", "solicitud"),
    )
    if q:
        cotizaciones = cotizaciones.filter(Q(numero__icontains=q) | Q(titulo__icontains=q) | Q(proyecto__nombre__icontains=q))
    if estado:
        cotizaciones = cotizaciones.filter(estado=estado)
    return render(
        request,
        "tienda/cliente/cotizaciones.html",
        {"cliente_usuario": cliente_usuario, "cotizaciones": cotizaciones, "q": q, "estado": estado, "estados": Cotizacion.ESTADOS},
    )


@cliente_portal_required
def cliente_cotizacion_detalle(request, cotizacion_id):
    cliente_usuario = cliente_usuario_actual(request)
    if not cliente_usuario.puede_ver_facturacion:
        raise PermissionDenied
    cotizacion = get_object_or_404(
        cotizaciones_para_cliente_usuario(
            cliente_usuario,
            Cotizacion.objects.select_related("cliente", "contacto", "proyecto", "solicitud"),
        ),
        pk=cotizacion_id,
        activa=True,
    )
    if not cotizacion.visible_para_cliente:
        raise Http404
    items = cotizacion.items.filter(activo=True).select_related("producto")
    return render(request, "tienda/cliente/cotizacion_detalle.html", {"cliente_usuario": cliente_usuario, "cotizacion": cotizacion, "items": items})


@cliente_portal_required
def cliente_perfil(request):
    cliente_usuario = cliente_usuario_actual(request)
    form = ClientePerfilForm(request.POST or None, instance=cliente_usuario.cliente)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Perfil actualizado.")
        return redirect("cliente_perfil")
    return render(request, "tienda/cliente/perfil.html", {"cliente_usuario": cliente_usuario, "form": form})


@cliente_portal_required
def cliente_notificaciones(request):
    cliente_usuario = cliente_usuario_actual(request)
    if request.method == "POST":
        notificacion_id = request.POST.get("notificacion_id")
        qs = cliente_usuario.notificaciones.all()
        if notificacion_id:
            qs = qs.filter(pk=notificacion_id)
        qs.update(leida=True)
        messages.success(request, "Notificaciones actualizadas.")
        return redirect("cliente_notificaciones")
    notificaciones = [
        notificacion
        for notificacion in cliente_usuario.notificaciones.select_related("solicitud", "proyecto")
        if notificacion_cliente_visible(cliente_usuario, notificacion)
    ]
    return render(request, "tienda/cliente/notificaciones.html", {"cliente_usuario": cliente_usuario, "notificaciones": notificaciones})


def panel_login(request):
    redirect_value = request.POST.get(REDIRECT_FIELD_NAME) or request.GET.get(REDIRECT_FIELD_NAME, "")
    if request.user.is_authenticated:
        if request.user.is_staff:
            return redirect("panel_dashboard")
        raise PermissionDenied
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        if not user.is_staff:
            form.add_error(None, "No tienes permisos para acceder al panel.")
        else:
            login(request, user)
            return redirect(safe_panel_redirect(request))
    return render(request, "tienda/panel/login.html", {"form": form, REDIRECT_FIELD_NAME: redirect_value})


@login_required(login_url="panel_login")
@require_POST
def panel_logout(request):
    logout(request)
    return redirect("panel_login")


@panel_staff_required
def panel_dashboard(request):
    hoy = timezone.localdate()
    siete_dias = hoy + timedelta(days=7)
    total = Solicitud.objects.count()
    nuevas = Solicitud.objects.filter(estado=Solicitud.ESTADO_NUEVA).count()
    revision = Solicitud.objects.filter(estado__in=[Solicitud.ESTADO_REVISION, Solicitud.ESTADO_PENDIENTE_INFO]).count()
    productos = Producto.objects.count()
    recientes = Solicitud.objects.select_related("producto").order_by("-creado")[:8]
    por_estado = Solicitud.objects.values("estado").annotate(total=Count("id")).order_by("estado")
    produccion = {
        "pendiente": Solicitud.objects.filter(estado_produccion=Solicitud.PROD_PENDIENTE_ASIGNAR).count(),
        "asignado": Solicitud.objects.filter(estado_produccion=Solicitud.PROD_ASIGNADO).count(),
        "en_proceso": Solicitud.objects.filter(estado_produccion=Solicitud.PROD_EN_PROCESO).count(),
        "con_novedad": Solicitud.objects.filter(estado_produccion=Solicitud.PROD_CON_NOVEDAD).count(),
        "terminado": Solicitud.objects.filter(estado_produccion=Solicitud.PROD_TERMINADO).count(),
    }
    estados_proyecto_activo = [
        Proyecto.ESTADO_PENDIENTE,
        Proyecto.ESTADO_PLANEACION,
        Proyecto.ESTADO_PRODUCCION,
        Proyecto.ESTADO_PAUSADO,
    ]
    tareas_pendientes_qs = SolicitudTarea.objects.filter(
        activa=True,
        estado__in=[SolicitudTarea.ESTADO_PENDIENTE, SolicitudTarea.ESTADO_ASIGNADA, SolicitudTarea.ESTADO_EN_PROCESO],
    )
    dashboard_cards = [
        {
            "titulo": "Solicitudes pendientes",
            "valor": Solicitud.objects.filter(estado_produccion=Solicitud.PROD_PENDIENTE_ASIGNAR).count(),
            "url": f"{reverse('panel_solicitudes')}?estado_produccion={Solicitud.PROD_PENDIENTE_ASIGNAR}",
            "icono": "SP",
            "estado": "Por asignar",
        },
        {
            "titulo": "Solicitudes en produccion",
            "valor": Solicitud.objects.filter(estado_produccion=Solicitud.PROD_EN_PROCESO).count(),
            "url": f"{reverse('panel_solicitudes')}?estado_produccion={Solicitud.PROD_EN_PROCESO}",
            "icono": "PR",
            "estado": "En curso",
        },
        {
            "titulo": "Cotizaciones abiertas",
            "valor": Cotizacion.objects.filter(activa=True, estado__in=[Cotizacion.ESTADO_BORRADOR, Cotizacion.ESTADO_ENVIADA, Cotizacion.ESTADO_VISTA]).count(),
            "url": f"{reverse('panel_cotizaciones')}?estado={Cotizacion.ESTADO_ENVIADA}",
            "icono": "CO",
            "estado": "Comercial",
        },
        {
            "titulo": "Cotizaciones por vencer",
            "valor": Cotizacion.objects.filter(activa=True, fecha_vencimiento__gte=hoy, fecha_vencimiento__lte=siete_dias).exclude(estado__in=[Cotizacion.ESTADO_APROBADA, Cotizacion.ESTADO_RECHAZADA, Cotizacion.ESTADO_ANULADA, Cotizacion.ESTADO_CONVERTIDA]).count(),
            "url": reverse("panel_cotizaciones"),
            "icono": "CV",
            "estado": "7 dias",
        },
        {
            "titulo": "Clientes activos",
            "valor": Cliente.objects.filter(activo=True).count(),
            "url": f"{reverse('panel_clientes')}?activo=1",
            "icono": "CL",
            "estado": "CRM",
        },
        {
            "titulo": "Proyectos activos",
            "valor": Proyecto.objects.filter(activo=True, estado__in=estados_proyecto_activo).count(),
            "url": f"{reverse('panel_proyectos')}?activo=1",
            "icono": "PY",
            "estado": "Operacion",
        },
        {
            "titulo": "Tareas pendientes",
            "valor": tareas_pendientes_qs.count(),
            "url": reverse("produccion_dashboard"),
            "icono": "TP",
            "estado": "Equipo",
        },
        {
            "titulo": "Tareas vencidas",
            "valor": tareas_pendientes_qs.filter(fecha_limite__lt=hoy).count(),
            "url": reverse("produccion_dashboard"),
            "icono": "TV",
            "estado": "Atencion",
        },
        {
            "titulo": "Pedidos con novedad",
            "valor": Solicitud.objects.filter(estado_produccion=Solicitud.PROD_CON_NOVEDAD).count(),
            "url": f"{reverse('panel_solicitudes')}?estado_produccion={Solicitud.PROD_CON_NOVEDAD}",
            "icono": "NV",
            "estado": "Revisar",
        },
        {
            "titulo": "Produccion en proceso",
            "valor": Solicitud.objects.filter(estado_produccion__in=[Solicitud.PROD_ASIGNADO, Solicitud.PROD_EN_PROCESO, Solicitud.PROD_CALIDAD]).count(),
            "url": reverse("produccion_dashboard"),
            "icono": "OP",
            "estado": "Planta",
        },
    ]
    return render(
        request,
        "tienda/panel/dashboard.html",
        {
            "total": total,
            "nuevas": nuevas,
            "revision": revision,
            "productos": productos,
            "recientes": recientes,
            "por_estado": por_estado,
            "produccion": produccion,
            "dashboard_cards": dashboard_cards,
        },
    )


@panel_staff_required
def centro_control(request):
    """Consolidado operativo sin sustituir los paneles especializados."""
    control = centro_control_snapshot(request.user)
    return render(
        request,
        "tienda/panel/centro_control.html",
        {
            "active": "panel_centro_control",
            "control": control,
        },
    )


@panel_staff_required
@require_POST
def centro_control_actualizar_alertas(request):
    result = refresh_control_alerts(request.user)
    messages.success(request, f"Alertas actualizadas: {result['created']} nuevas.")
    return redirect("panel_centro_control")


@panel_staff_required
def solicitudes_lista(request):
    q = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "").strip()
    estado_produccion = request.GET.get("estado_produccion", "").strip()
    solicitudes = Solicitud.objects.select_related("producto", "cliente", "contacto", "proyecto").all()
    if q:
        
        filtro = Q(cliente_nombre__icontains=q) | Q(cliente_celular__icontains=q) | Q(producto__nombre__icontains=q)
        if q.isdigit():
            filtro = filtro | Q(id=int(q))
        solicitudes = solicitudes.filter(filtro)
    if estado:
        solicitudes = solicitudes.filter(estado=estado)
    if estado_produccion:
        solicitudes = solicitudes.filter(estado_produccion=estado_produccion)
    return render(
        request,
        "tienda/panel/solicitudes.html",
        {
            "solicitudes": solicitudes,
            "estados": Solicitud.ESTADOS,
            "estados_produccion": Solicitud.ESTADOS_PRODUCCION,
            "q": q,
            "estado": estado,
            "estado_produccion": estado_produccion,
        },
    )


@panel_staff_required
def cotizaciones_lista(request):
    q = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "").strip()
    cliente_id = request.GET.get("cliente", "").strip()
    proyecto_id = request.GET.get("proyecto", "").strip()
    fecha = request.GET.get("fecha", "").strip()
    cotizaciones = Cotizacion.objects.select_related("cliente", "contacto", "proyecto", "solicitud", "creada_por")
    if q:
        filtro = (
            Q(numero__icontains=q)
            | Q(titulo__icontains=q)
            | Q(cliente__nombre__icontains=q)
            | Q(cliente__razon_social__icontains=q)
            | Q(proyecto__nombre__icontains=q)
        )
        if q.isdigit():
            filtro |= Q(id=int(q)) | Q(solicitud_id=int(q))
        cotizaciones = cotizaciones.filter(filtro)
    if estado:
        cotizaciones = cotizaciones.filter(estado=estado)
    if cliente_id.isdigit():
        cotizaciones = cotizaciones.filter(cliente_id=cliente_id)
    if proyecto_id.isdigit():
        cotizaciones = cotizaciones.filter(proyecto_id=proyecto_id)
    if fecha:
        cotizaciones = cotizaciones.filter(fecha_creacion__date=fecha)
    clientes = Cliente.objects.filter(activo=True).order_by("nombre", "razon_social")
    proyectos = Proyecto.objects.filter(activo=True).select_related("cliente").order_by("nombre")
    return render(
        request,
        "tienda/panel/cotizaciones.html",
        {
            "cotizaciones": cotizaciones,
            "q": q,
            "estado": estado,
            "cliente_id": cliente_id,
            "proyecto_id": proyecto_id,
            "fecha": fecha,
            "estados": Cotizacion.ESTADOS,
            "clientes": clientes,
            "proyectos": proyectos,
        },
    )


def cotizacion_initial_from_request(request):
    initial = {}
    cliente_id = request.GET.get("cliente", "").strip()
    proyecto_id = request.GET.get("proyecto", "").strip()
    solicitud_id = request.GET.get("solicitud", "").strip()
    if cliente_id.isdigit():
        initial["cliente"] = cliente_id
    if proyecto_id.isdigit():
        proyecto = Proyecto.objects.select_related("cliente", "contacto").filter(pk=proyecto_id).first()
        if proyecto:
            initial["proyecto"] = proyecto.id
            if proyecto.cliente_id:
                initial["cliente"] = proyecto.cliente_id
            if proyecto.contacto_id:
                initial["contacto"] = proyecto.contacto_id
            initial.setdefault("titulo", f"Cotización {proyecto.nombre}")
    if solicitud_id.isdigit():
        solicitud = Solicitud.objects.select_related("producto", "cliente", "contacto", "proyecto__cliente", "proyecto__contacto").filter(pk=solicitud_id).first()
        if solicitud:
            initial["solicitud"] = solicitud.id
            initial["proyecto"] = solicitud.proyecto_id
            if solicitud.cliente_id:
                initial["cliente"] = solicitud.cliente_id
            elif solicitud.proyecto_id and solicitud.proyecto.cliente_id:
                initial["cliente"] = solicitud.proyecto.cliente_id
            if solicitud.contacto_id:
                initial["contacto"] = solicitud.contacto_id
            elif solicitud.proyecto_id and solicitud.proyecto.contacto_id:
                initial["contacto"] = solicitud.proyecto.contacto_id
            initial.setdefault("titulo", f"Cotización OP-{solicitud.id:06d} - {solicitud.producto.nombre}")
            initial.setdefault("descripcion", f"Solicitud OP-{solicitud.id:06d}")
    if initial.get("cliente") and not initial.get("contacto"):
        contacto = cliente_contacto_principal(Cliente.objects.filter(pk=initial["cliente"]).first())
        if contacto:
            initial["contacto"] = contacto.id
    return initial


def contacto_option(contacto):
    detalle = contacto.email or contacto.whatsapp or contacto.telefono or "Sin datos de contacto"
    return {
        "id": contacto.id,
        "label": f"{contacto.nombre} · {detalle}",
        "client_id": contacto.cliente_id,
        "is_principal": contacto.es_principal,
    }


def proyecto_option(proyecto):
    return {
        "id": proyecto.id,
        "label": f"{proyecto.nombre} · {proyecto.get_estado_display()}",
        "client_id": proyecto.cliente_id,
    }


def punto_venta_option(punto_venta):
    return {
        "id": punto_venta.id,
        "label": f"{punto_venta.nombre} · {punto_venta.ciudad or 'Sin ciudad'}",
        "client_id": punto_venta.cliente_id,
        "is_principal": punto_venta.es_principal,
    }


def solicitud_option(solicitud):
    producto = solicitud.producto.nombre if solicitud.producto_id else "Solicitud"
    proyecto = f" · {solicitud.proyecto.nombre}" if solicitud.proyecto_id else ""
    return {
        "id": solicitud.id,
        "label": f"OP-{solicitud.id:06d} · {producto}{proyecto}",
        "client_id": solicitud.cliente_id or getattr(solicitud.proyecto, "cliente_id", None),
        "project_id": solicitud.proyecto_id,
    }


@panel_staff_required
def panel_ajax_cliente_contactos(request, cliente_id):
    contactos = ClienteContacto.objects.filter(
        activo=True,
        cliente_id=cliente_id,
    ).select_related("cliente").order_by("-es_principal", "nombre")
    return JsonResponse({"results": [contacto_option(contacto) for contacto in contactos]})


@panel_staff_required
def panel_ajax_cliente_proyectos(request, cliente_id):
    proyectos = Proyecto.objects.filter(
        activo=True,
        cliente_id=cliente_id,
    ).select_related("cliente").order_by("-fecha_creacion", "nombre")
    return JsonResponse({"results": [proyecto_option(proyecto) for proyecto in proyectos]})


@panel_staff_required
def panel_ajax_cliente_puntos_venta(request, cliente_id):
    puntos = ClientePuntoVenta.objects.filter(activo=True, cliente_id=cliente_id).select_related("cliente").order_by("-es_principal", "nombre")
    return JsonResponse({"results": [punto_venta_option(punto) for punto in puntos]})


@panel_staff_required
def panel_ajax_cliente_solicitudes(request, cliente_id):
    solicitudes = (
        Solicitud.objects.filter(Q(cliente_id=cliente_id) | Q(proyecto__cliente_id=cliente_id))
        .select_related("producto", "cliente", "proyecto")
        .distinct()
        .order_by("-creado")
    )
    return JsonResponse({"results": [solicitud_option(solicitud) for solicitud in solicitudes]})


@panel_staff_required
def panel_ajax_proyecto_solicitudes(request, proyecto_id):
    solicitudes = (
        Solicitud.objects.filter(proyecto_id=proyecto_id)
        .select_related("producto", "cliente", "proyecto")
        .order_by("-creado")
    )
    return JsonResponse({"results": [solicitud_option(solicitud) for solicitud in solicitudes]})


def crear_item_desde_solicitud(cotizacion):
    if not cotizacion.solicitud_id or cotizacion.items.exists():
        return None
    solicitud = cotizacion.solicitud
    detalle = []
    for respuesta in respuestas_tecnicas_solicitud(solicitud):
        if respuesta.archivo:
            detalle.append(f"{respuesta.etiqueta}: {respuesta.archivo.name}")
        elif respuesta.valor_texto:
            detalle.append(f"{respuesta.etiqueta}: {respuesta.valor_texto}")
    return CotizacionItem.objects.create(
        cotizacion=cotizacion,
        producto=solicitud.producto,
        descripcion=solicitud.producto.nombre,
        detalle="\n".join(detalle),
        cantidad=Decimal("1"),
        valor_unitario=solicitud.precio_final or solicitud.precio_estimado or Decimal("0"),
        unidad="servicio",
        orden=1,
    )


@panel_staff_required
def cotizacion_crear(request):
    form = CotizacionForm(request.POST or None, initial=cotizacion_initial_from_request(request))
    if request.method == "POST" and form.is_valid():
        cotizacion = form.save(commit=False)
        cotizacion.creada_por = request.user
        cotizacion.actualizada_por = request.user
        cotizacion.save()
        crear_item_desde_solicitud(cotizacion)
        messages.success(request, "Cotización creada.")
        return redirect("panel_cotizacion_detalle", cotizacion_id=cotizacion.id)
    return render(request, "tienda/panel/cotizacion_form.html", {"form": form, "titulo": "Crear cotización"})


@panel_staff_required
def cotizacion_editar(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion.objects.select_related("cliente", "proyecto", "solicitud"), pk=cotizacion_id)
    form = CotizacionForm(request.POST or None, instance=cotizacion)
    if request.method == "POST" and form.is_valid():
        cotizacion = form.save(commit=False)
        cotizacion.actualizada_por = request.user
        cotizacion.save()
        messages.success(request, "Cotización actualizada.")
        return redirect("panel_cotizacion_detalle", cotizacion_id=cotizacion.id)
    return render(request, "tienda/panel/cotizacion_form.html", {"form": form, "cotizacion": cotizacion, "titulo": f"Editar {cotizacion.numero}"})


@panel_staff_required
def cotizacion_detalle(request, cotizacion_id):
    cotizacion = get_object_or_404(
        Cotizacion.objects.select_related("cliente", "contacto", "proyecto", "solicitud__producto", "creada_por", "actualizada_por"),
        pk=cotizacion_id,
    )
    item_form = CotizacionItemForm(cotizacion=cotizacion)
    estado_form = CotizacionEstadoForm(initial={"estado": cotizacion.estado})
    items = cotizacion.items.select_related("producto").order_by("orden", "id")
    return render(
        request,
        "tienda/panel/cotizacion_detalle.html",
        {"cotizacion": cotizacion, "items": items, "item_form": item_form, "estado_form": estado_form, "estados": Cotizacion.ESTADOS},
    )


@panel_staff_required
def cotizacion_item_crear(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion, pk=cotizacion_id)
    form = CotizacionItemForm(request.POST or None, cotizacion=cotizacion)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Ítem agregado.")
        return redirect("panel_cotizacion_detalle", cotizacion_id=cotizacion.id)
    return render(request, "tienda/panel/cotizacion_item_form.html", {"form": form, "cotizacion": cotizacion, "titulo": "Agregar ítem"})


@panel_staff_required
def cotizacion_item_editar(request, item_id):
    item = get_object_or_404(CotizacionItem.objects.select_related("cotizacion"), pk=item_id)
    form = CotizacionItemForm(request.POST or None, instance=item, cotizacion=item.cotizacion)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Ítem actualizado.")
        return redirect("panel_cotizacion_detalle", cotizacion_id=item.cotizacion_id)
    return render(request, "tienda/panel/cotizacion_item_form.html", {"form": form, "cotizacion": item.cotizacion, "item": item, "titulo": "Editar ítem"})


@panel_staff_required
@require_POST
def cotizacion_item_toggle(request, item_id):
    item = get_object_or_404(CotizacionItem.objects.select_related("cotizacion"), pk=item_id)
    item.activo = not item.activo
    item.save(update_fields=["activo"])
    messages.success(request, "Ítem actualizado.")
    return redirect("panel_cotizacion_detalle", cotizacion_id=item.cotizacion_id)


@panel_staff_required
@require_POST
def cotizacion_cambiar_estado(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion.objects.select_related("solicitud", "cliente"), pk=cotizacion_id)
    estado_anterior = cotizacion.estado
    form = CotizacionEstadoForm(request.POST)
    if form.is_valid():
        nuevo_estado = form.cleaned_data["estado"]
        cotizacion.estado = nuevo_estado
        cotizacion.actualizada_por = request.user
        if nuevo_estado == Cotizacion.ESTADO_ENVIADA and not cotizacion.fecha_emision:
            cotizacion.fecha_emision = timezone.localdate()
        if nuevo_estado == Cotizacion.ESTADO_ENVIADA and not cotizacion.fecha_vencimiento:
            cotizacion.fecha_vencimiento = timezone.localdate() + timedelta(days=cotizacion.validez_dias)
        cotizacion.save()
        if cotizacion.solicitud_id:
            crear_novedad(
                cotizacion.solicitud,
                request.user,
                SolicitudNovedad.TIPO_SISTEMA,
                form.cleaned_data.get("comentario") or f"Cotización {cotizacion.numero} cambió de estado: {estado_anterior} -> {nuevo_estado}.",
            )
        messages.success(request, "Estado de cotización actualizado.")
    else:
        messages.error(request, "No se pudo cambiar el estado.")
    return redirect("panel_cotizacion_detalle", cotizacion_id=cotizacion.id)


@panel_staff_required
@require_POST
def cotizacion_duplicar(request, cotizacion_id):
    original = get_object_or_404(Cotizacion.objects.prefetch_related("items"), pk=cotizacion_id)
    nueva = Cotizacion.objects.create(
        cliente=original.cliente,
        contacto=original.contacto,
        proyecto=original.proyecto,
        solicitud=original.solicitud,
        titulo=f"Copia de {original.titulo}",
        descripcion=original.descripcion,
        moneda=original.moneda,
        observaciones_cliente=original.observaciones_cliente,
        condiciones_comerciales=original.condiciones_comerciales,
        tiempo_entrega=original.tiempo_entrega,
        forma_pago=original.forma_pago,
        garantia=original.garantia,
        validez_dias=original.validez_dias,
        creada_por=request.user,
        actualizada_por=request.user,
    )
    for item in original.items.all():
        CotizacionItem.objects.create(
            cotizacion=nueva,
            producto=item.producto,
            descripcion=item.descripcion,
            detalle=item.detalle,
            cantidad=item.cantidad,
            unidad=item.unidad,
            valor_unitario=item.valor_unitario,
            descuento_porcentaje=item.descuento_porcentaje,
            descuento_valor=item.descuento_valor,
            impuesto_porcentaje=item.impuesto_porcentaje,
            orden=item.orden,
            activo=item.activo,
        )
    messages.success(request, f"Cotización duplicada como {nueva.numero}.")
    return redirect("panel_cotizacion_detalle", cotizacion_id=nueva.id)


@panel_staff_required
def cotizacion_pdf(request, cotizacion_id):
    cotizacion = get_object_or_404(
        Cotizacion.objects.select_related("cliente", "contacto", "proyecto", "solicitud__producto", "creada_por"),
        pk=cotizacion_id,
    )
    cotizacion.recalcular_totales()
    items = cotizacion.items.filter(activo=True).select_related("producto")
    return render(request, "tienda/panel/cotizacion_pdf.html", {"cotizacion": cotizacion, "items": items})


def destinatario_cotizacion(cotizacion, email_override=""):
    emails_permitidos = {}
    if cotizacion.contacto_id and cotizacion.contacto.email:
        emails_permitidos[cotizacion.contacto.email.strip().lower()] = (
            cotizacion.contacto.email.strip(),
            cotizacion.contacto.nombre,
        )
    if cotizacion.cliente.email:
        emails_permitidos[cotizacion.cliente.email.strip().lower()] = (
            cotizacion.cliente.email.strip(),
            cotizacion.cliente.contacto_principal or str(cotizacion.cliente),
        )
    for contacto in cotizacion.cliente.contactos.filter(activo=True).exclude(email=""):
        emails_permitidos.setdefault(
            contacto.email.strip().lower(),
            (contacto.email.strip(), contacto.nombre),
        )

    if email_override:
        key = email_override.strip().lower()
        if key not in emails_permitidos:
            return "", "", "El email destino no pertenece a un contacto activo de este cliente."
        email, nombre = emails_permitidos[key]
        return email, nombre, ""

    if cotizacion.contacto_id and cotizacion.contacto.email:
        return cotizacion.contacto.email.strip(), cotizacion.contacto.nombre, ""
    if cotizacion.cliente.email:
        return cotizacion.cliente.email.strip(), cotizacion.cliente.contacto_principal or str(cotizacion.cliente), ""
    return "", "", "El cliente no tiene email para enviar la cotizacion."


@panel_staff_required
@require_POST
def cotizacion_enviar(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion.objects.select_related("cliente", "contacto", "solicitud", "proyecto"), pk=cotizacion_id)
    if not cotizacion.activa or cotizacion.estado == Cotizacion.ESTADO_ANULADA:
        messages.error(request, "Esta cotizacion no esta activa para envio.")
        return redirect("panel_cotizacion_detalle", cotizacion_id=cotizacion.id)
    if not cotizacion.items.filter(activo=True).exists():
        messages.error(request, "Agrega al menos un item activo antes de enviar la cotizacion.")
        return redirect("panel_cotizacion_detalle", cotizacion_id=cotizacion.id)

    email, nombre_destinatario, error_destinatario = destinatario_cotizacion(cotizacion, request.POST.get("email", "").strip())
    if error_destinatario:
        messages.error(request, error_destinatario)
        return redirect("panel_cotizacion_detalle", cotizacion_id=cotizacion.id)
    cotizacion.recalcular_totales()
    if not cotizacion.fecha_emision:
        cotizacion.fecha_emision = timezone.localdate()
    if not cotizacion.fecha_vencimiento:
        cotizacion.fecha_vencimiento = timezone.localdate() + timedelta(days=cotizacion.validez_dias)
    try:
        pdf_bytes = generar_pdf_cotizacion(cotizacion)
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect("panel_cotizacion_detalle", cotizacion_id=cotizacion.id)

    enviado = enviar_correo_cliente(
        email,
        f"Cotización {cotizacion.numero}",
        "tienda/emails/cotizacion_cliente.html",
        {
            "cotizacion": cotizacion,
            "cliente": cotizacion.cliente,
            "nombre_destinatario": nombre_destinatario,
            "preview_text": f"Adjuntamos la cotizacion {cotizacion.numero}.",
        },
        adjuntos=[
            {
                "filename": nombre_archivo_cotizacion(cotizacion),
                "content": pdf_bytes,
                "mimetype": "application/pdf",
            }
        ],
    )
    if enviado:
        cotizacion.estado = Cotizacion.ESTADO_ENVIADA
        cotizacion.fecha_envio = timezone.now()
        cotizacion.enviada_a_email = email
        cotizacion.actualizada_por = request.user
        cotizacion.save()
        if cotizacion.solicitud_id:
            crear_novedad(
                cotizacion.solicitud,
                request.user,
                SolicitudNovedad.TIPO_SISTEMA,
                f"Cotizacion {cotizacion.numero} enviada a {email} con PDF adjunto.",
            )
        for cliente_usuario in cotizacion.cliente.usuarios_portal.filter(activo=True, user__is_active=True):
            if not cliente_usuario.puede_ver_facturacion or not cliente_usuario_puede_ver_cotizacion(cliente_usuario, cotizacion):
                continue
            crear_notificacion_cliente(
                cliente_usuario,
                f"Cotización {cotizacion.numero}",
                "Tienes una cotización disponible en tu portal.",
                tipo=NotificacionCliente.TIPO_FACTURACION,
                url_destino=reverse("cliente_cotizacion_detalle", args=[cotizacion.id]),
                enviar_email=False,
            )
        messages.success(request, "Cotizacion enviada con PDF adjunto.")
    else:
        messages.error(request, "No se pudo enviar el correo. Revisa la configuración SMTP.")
    return redirect("panel_cotizacion_detalle", cotizacion_id=cotizacion.id)


@panel_staff_required
@require_POST
def cotizacion_convertir(request, cotizacion_id):
    with transaction.atomic():
        cotizacion = get_object_or_404(Cotizacion.objects.select_for_update().select_related("solicitud"), pk=cotizacion_id)
        if cotizacion.estado not in {Cotizacion.ESTADO_APROBADA, Cotizacion.ESTADO_CONVERTIDA}:
            messages.error(request, "Solo una cotización aprobada puede convertirse.")
            return redirect("panel_cotizacion_detalle", cotizacion_id=cotizacion.id)
        venta = Venta.objects.filter(cotizacion=cotizacion).first()
        if not venta:
            items = list(cotizacion.items.filter(activo=True).select_related("producto"))
            if not items:
                messages.error(request, "La cotización debe tener al menos un ítem activo.")
                return redirect("panel_cotizacion_detalle", cotizacion_id=cotizacion.id)
            venta = Venta.objects.create(
                cliente=cotizacion.cliente,
                punto_venta=cotizacion.punto_venta,
                proyecto=cotizacion.proyecto,
                cotizacion=cotizacion,
                solicitud=cotizacion.solicitud,
                fecha_venta=timezone.localdate(),
                responsable=request.user,
                creado_por=request.user,
                actualizado_por=request.user,
                estado=Venta.ESTADO_CONFIRMADA,
                observaciones=cotizacion.condiciones_comerciales,
            )
            for item in items:
                VentaItem.objects.create(
                    venta=venta,
                    producto=item.producto,
                    descripcion=item.descripcion,
                    cantidad=item.cantidad,
                    unidad=item.unidad,
                    precio_unitario=item.valor_unitario,
                    descuento=item.descuento_calculado,
                    impuesto=item.impuesto_calculado,
                    orden=item.orden,
                    activo=item.activo,
                )
            venta.recalcular_totales()
        cotizacion.estado = Cotizacion.ESTADO_CONVERTIDA
        cotizacion.actualizada_por = request.user
        cotizacion.save()
        if cotizacion.solicitud_id:
            cotizacion.solicitud.precio_final = cotizacion.total
            cotizacion.solicitud.estado = Solicitud.ESTADO_COTIZADA
            cotizacion.solicitud.save(update_fields=["precio_final", "estado", "actualizado"])
            crear_novedad(cotizacion.solicitud, request.user, SolicitudNovedad.TIPO_SISTEMA, f"Cotización {cotizacion.numero} convertida a venta {venta.numero}.")
    messages.success(request, f"Venta {venta.numero} confirmada desde la cotización.")
    return redirect("panel_cotizacion_detalle", cotizacion_id=cotizacion.id)


@panel_staff_required
def ventas_lista(request):
    ventas = Venta.objects.select_related("cliente", "punto_venta", "proyecto", "responsable").prefetch_related("facturas_alegra__factura")
    query = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "").strip()
    cliente_id = request.GET.get("cliente", "").strip()
    if query:
        ventas = ventas.filter(Q(numero__icontains=query) | Q(cliente__nombre__icontains=query) | Q(cliente__razon_social__icontains=query))
    if estado in {key for key, _label in Venta.ESTADOS}:
        ventas = ventas.filter(estado=estado)
    if cliente_id.isdigit():
        ventas = ventas.filter(cliente_id=cliente_id)
    page = Paginator(ventas, 25).get_page(request.GET.get("page"))
    return render(request, "tienda/panel/ventas.html", {"page_obj": page, "query": query, "estado": estado, "cliente_id": cliente_id, "estados": Venta.ESTADOS, "clientes": Cliente.objects.filter(activo=True).order_by("nombre")})


@panel_staff_required
def venta_crear(request):
    form = VentaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            venta = form.save(commit=False)
            venta.creado_por = request.user
            venta.actualizado_por = request.user
            venta.responsable = request.user
            venta.save()
        messages.success(request, f"Venta {venta.numero} creada como borrador.")
        return redirect("panel_venta_detalle", venta_id=venta.id)
    return render(request, "tienda/panel/venta_form.html", {"form": form, "titulo": "Nueva venta"})


@panel_staff_required
def venta_detalle(request, venta_id):
    venta = get_object_or_404(Venta.objects.select_related("cliente", "punto_venta", "proyecto", "cotizacion", "solicitud", "responsable", "creado_por").prefetch_related("items__producto", "facturas_alegra__factura"), pk=venta_id)
    return render(request, "tienda/panel/venta_detalle.html", {"venta": venta, "item_form": VentaItemForm(venta=venta), "estados": Venta.ESTADOS})


@panel_staff_required
def venta_item_crear(request, venta_id):
    venta = get_object_or_404(Venta, pk=venta_id)
    form = VentaItemForm(request.POST or None, venta=venta)
    if request.method == "POST" and form.is_valid():
        form.save()
        venta.actualizado_por = request.user
        venta.save(update_fields=["actualizado_por", "actualizado"])
        messages.success(request, "Ítem agregado a la venta.")
        return redirect("panel_venta_detalle", venta_id=venta.id)
    return render(request, "tienda/panel/venta_item_form.html", {"form": form, "venta": venta, "titulo": "Agregar ítem de venta"})


@panel_staff_required
@require_POST
def venta_confirmar(request, venta_id):
    venta = get_object_or_404(Venta, pk=venta_id)
    if venta.estado == Venta.ESTADO_CANCELADA or not venta.items.filter(activo=True).exists():
        messages.error(request, "La venta cancelada o sin ítems activos no puede confirmarse.")
    else:
        venta.estado = Venta.ESTADO_CONFIRMADA
        venta.actualizado_por = request.user
        venta.save(update_fields=["estado", "actualizado_por", "actualizado"])
        messages.success(request, f"Venta {venta.numero} confirmada. Esto no implica facturación ni pago.")
    return redirect("panel_venta_detalle", venta_id=venta.id)


@panel_staff_required
@require_POST
def venta_cancelar(request, venta_id):
    venta = get_object_or_404(Venta, pk=venta_id)
    motivo = request.POST.get("motivo", "").strip()
    if not motivo:
        messages.error(request, "Debes indicar el motivo de cancelación.")
    else:
        venta.estado = Venta.ESTADO_CANCELADA
        venta.cancelado_por = request.user
        venta.actualizado_por = request.user
        venta.motivo_cancelacion = motivo[:500]
        venta.save(update_fields=["estado", "cancelado_por", "actualizado_por", "motivo_cancelacion", "actualizado"])
        messages.success(request, "Venta cancelada localmente.")
    return redirect("panel_venta_detalle", venta_id=venta.id)


@panel_staff_required
def ventas_informes(request):
    ventas = Venta.objects.all()
    facturas = AlegraInvoiceStaging.objects.all()
    factura_summary = facturacion_summary(queryset=facturas)
    freshness = source_freshness("invoices")
    reportable_ids = factura_summary["reportable_ids"]
    return render(request, "tienda/panel/ventas_informes.html", {
        "ventas_confirmadas": ventas.filter(estado__in=[Venta.ESTADO_CONFIRMADA, Venta.ESTADO_PROCESO, Venta.ESTADO_COMPLETADA]).count(),
        "valor_vendido": ventas.filter(estado__in=[Venta.ESTADO_CONFIRMADA, Venta.ESTADO_PROCESO, Venta.ESTADO_COMPLETADA]).aggregate(total=Sum("total"))["total"] or 0,
        "facturas_count": factura_summary["reportable_count"],
        "facturado": factura_summary["eligible_total"],
        "facturas_fuera_indicadores": factura_summary["excluded_documents"],
        "facturas_fuera_indicadores_count": len(factura_summary["excluded_documents"]),
        "financial_freshness": freshness,
        "financial_warning": freshness["warning"],
        "financial_definitive": freshness["is_definitive"],
        "ventas_vinculadas": ventas.filter(facturas_alegra__isnull=False).distinct().count(),
        "ventas_sin_factura": ventas.filter(facturas_alegra__isnull=True).count(),
        "facturas_sin_venta": facturas.filter(pk__in=reportable_ids, ventas_betta__isnull=True).count(),
        "ultima_sincronizacion": facturas.order_by("-last_synced_at").values_list("last_synced_at", flat=True).first(),
    })


@panel_staff_required
def cartera_dashboard(request):
    invoices = AlegraInvoiceStaging.objects.select_related("matched_client").order_by("-issue_date", "-id")
    query = request.GET.get("q", "").strip()
    client_id = request.GET.get("cliente", "").strip()
    state = request.GET.get("estado", "").strip()
    aging = request.GET.get("antiguedad", "").strip()
    if query:
        invoices = invoices.filter(Q(number__icontains=query) | Q(client_name__icontains=query) | Q(client_identification__icontains=query))
    if client_id.isdigit():
        invoices = invoices.filter(matched_client_id=client_id)
    rows = cartera_rows(queryset=invoices)
    if state:
        rows = [row for row in rows if (row["is_overdue"] if state == STATUS_OVERDUE else row["payment_status"] == state)]
    if aging:
        rows = [row for row in rows if row["aging"] == aging]
    summary = cartera_summary(queryset=invoices, rows=[row for row in rows if row["included"]])
    freshness = financial_freshness()
    summary["overdue_percentage"] = (summary["overdue_balance"] / summary["known_balance"] * 100) if summary["known_balance"] else 0
    return render(request, "tienda/panel/cartera.html", {"rows": rows, "summary": summary, "query": query, "client_id": client_id, "state": state, "aging": aging, "financial_freshness": freshness, "financial_warnings": freshness["warnings"], "financial_warning": " ".join(freshness["warnings"]), "financial_definitive": freshness["current"], "clientes": Cliente.objects.filter(activo=True).order_by("nombre"), "payments": AlegraPaymentStaging.objects.select_related("matched_client").order_by("-payment_date", "-id")[:20], "responsables": CarteraResponsable.objects.filter(activo=True).select_related("cliente", "responsable"), "status_choices": [(STATUS_PENDING, "Pendiente"), (STATUS_OVERDUE, "Vencida"), (STATUS_PARTIAL, "Parcialmente pagada"), (STATUS_PAID, "Pagada"), (STATUS_INSUFFICIENT, "Sin información")], "aging_choices": [("no_vencida", "No vencida"), ("1_30", "1–30 días"), ("31_60", "31–60 días"), ("61_90", "61–90 días"), ("mas_90", "Más de 90 días"), ("sin_informacion", "Sin información")]})


@panel_staff_required
def cartera_cliente_detalle(request, cliente_id):
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    invoices = AlegraInvoiceStaging.objects.filter(matched_client=cliente).select_related("matched_client")
    summary = cartera_summary(queryset=invoices)
    freshness = financial_freshness()
    return render(request, "tienda/panel/cartera_cliente.html", {"cliente": cliente, "rows": summary["rows"], "summary": summary, "financial_freshness": freshness, "financial_warnings": freshness["warnings"], "financial_warning": " ".join(freshness["warnings"]), "financial_definitive": freshness["current"], "payments": AlegraPaymentStaging.objects.filter(matched_client=cliente).order_by("-payment_date", "-id"), "gestiones": cliente.gestiones_cartera.select_related("responsable", "factura"), "compromisos": cliente.compromisos_pago.select_related("responsable", "factura"), "asignacion": cliente.asignaciones_cartera.filter(activo=True).select_related("responsable").first(), "gestion_form": CarteraGestionForm(initial={"cliente": cliente}), "compromiso_form": CompromisoPagoForm(initial={"cliente": cliente}), "responsable_form": CarteraResponsableForm(initial={"cliente": cliente, "activo": True}), "responsable_users": get_user_model().objects.filter(is_active=True).order_by("username")})


@panel_staff_required
def cartera_gestion_crear(request, cliente_id):
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    form = CarteraGestionForm(request.POST or None, initial={"cliente": cliente})
    if request.method == "POST" and form.is_valid():
        gestion = form.save(commit=False)
        gestion.creado_por = request.user
        gestion.save()
        messages.success(request, "Gestión de cobranza registrada.")
    return redirect("panel_cartera_cliente", cliente_id=cliente.id)


@panel_staff_required
def cartera_compromiso_crear(request, cliente_id):
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    form = CompromisoPagoForm(request.POST or None, initial={"cliente": cliente})
    if request.method == "POST" and form.is_valid():
        compromiso = form.save(commit=False)
        compromiso.creado_por = request.user
        compromiso.save()
        messages.success(request, "Compromiso de pago registrado. No modifica el saldo contable.")
    return redirect("panel_cartera_cliente", cliente_id=cliente.id)


@panel_staff_required
@require_POST
def cartera_asignar_responsable(request, cliente_id):
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    responsable_id = request.POST.get("responsable")
    with transaction.atomic():
        CarteraResponsable.objects.filter(cliente=cliente, activo=True).update(activo=False)
        if responsable_id:
            CarteraResponsable.objects.create(cliente=cliente, responsable_id=responsable_id, asignado_por=request.user)
    messages.success(request, "Responsable de cartera actualizado.")
    return redirect("panel_cartera_cliente", cliente_id=cliente.id)


@alegra_change_required
@require_POST
def alegra_sync_payments(request):
    try:
        limit = max(1, min(int(request.POST.get("limit", "100")), 300))
    except ValueError:
        limit = 100
    result = AlegraPaymentImporter().sync(limit=limit, actor=request.user)
    if result["errors"]:
        messages.error(request, f"No se pudo consultar pagos recibidos: {result['errors'][0]} Los registros anteriores se conservaron.")
    else:
        messages.success(request, f"Pagos de ingreso consultados: {result['total']}; nuevos: {result['created']}; actualizados: {result['updated']}.")
    return redirect("panel_cartera")


@alegra_staff_required
def alegra_facturas_catalogo(request):
    facturas = AlegraInvoiceStaging.objects.select_related("matched_client").order_by("-issue_date", "-id")
    query = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "").strip()
    cliente_id = request.GET.get("cliente", "").strip()
    if query:
        facturas = facturas.filter(Q(number__icontains=query) | Q(client_name__icontains=query) | Q(client_identification__icontains=query))
    if estado in {key for key, _label in AlegraInvoiceStaging.STATUSES}:
        facturas = facturas.filter(external_status=estado)
    if cliente_id.isdigit():
        facturas = facturas.filter(matched_client_id=cliente_id)
    page = Paginator(facturas, 25).get_page(request.GET.get("page"))
    freshness = source_freshness("invoices")
    return render(request, "tienda/panel/alegra_facturas.html", {"page_obj": page, "query": query, "estado": estado, "cliente_id": cliente_id, "estados": AlegraInvoiceStaging.STATUSES, "clientes": Cliente.objects.filter(activo=True).order_by("nombre"), "ultima_sincronizacion": AlegraInvoiceStaging.objects.order_by("-last_synced_at").values_list("last_synced_at", flat=True).first(), "financial_freshness": freshness, "financial_warning": freshness["warning"]})


@alegra_change_required
@require_POST
def alegra_sync_invoices(request):
    try:
        limit = max(1, min(int(request.POST.get("limit", "100")), 300))
    except ValueError:
        limit = 100
    result = AlegraInvoiceImporter().sync(limit=limit, actor=request.user)
    if result["errors"]:
        messages.error(request, f"No se pudo completar la consulta de facturas: {result['errors'][0]} Los registros anteriores se conservaron.")
    else:
        messages.success(request, f"Facturas consultadas: {result['total']}; nuevas: {result['created']}; actualizadas: {result['updated']}; cambios detectados: {result['changed']}.")
    return redirect("alegra_facturas_catalogo")


@alegra_staff_required
def alegra_factura_detalle(request, staging_id):
    factura = get_object_or_404(AlegraInvoiceStaging.objects.select_related("matched_client").prefetch_related("ventas_betta__venta"), pk=staging_id)
    freshness = financial_freshness()
    return render(request, "tienda/panel/alegra_factura_detalle.html", {"factura": factura, "cartera_row": invoice_cartera_row(factura), "financial_freshness": freshness, "financial_warnings": freshness["warnings"], "financial_warning": " ".join(freshness["warnings"]), "ventas": Venta.objects.order_by("-fecha_venta", "-id")[:200]})


@alegra_change_required
@require_POST
def alegra_factura_vincular_venta(request, staging_id):
    evidencia = request.POST.get("evidencia", "").strip()
    try:
        link_invoice_to_sale(staging_id, request.POST.get("venta_id"), evidence=evidencia, actor=request.user)
        messages.success(request, "Factura vinculada manualmente a la venta.")
    except (ValidationError, ValueError, Venta.DoesNotExist, AlegraInvoiceStaging.DoesNotExist) as exc:
        messages.error(request, str(exc))
    return redirect("alegra_factura_detalle", staging_id=staging_id)


@panel_staff_required
def categorias_lista(request):
    q = request.GET.get("q", "").strip()
    activa = request.GET.get("activa", "").strip()
    categorias = Categoria.objects.annotate(num_productos=Count("productos", distinct=True))
    if q:
        categorias = categorias.filter(Q(nombre__icontains=q) | Q(slug__icontains=q))
    if activa in ["1", "0"]:
        categorias = categorias.filter(activa=activa == "1")
    categorias = categorias.order_by("orden", "nombre")
    return render(
        request,
        "tienda/panel/categorias.html",
        {"categorias": categorias, "q": q, "activa": activa},
    )


@panel_staff_required
def categoria_crear(request):
    form = CategoriaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Categoría creada.")
        return redirect("panel_categorias")
    return render(request, "tienda/panel/categoria_form.html", {"form": form, "titulo": "Crear categoría"})


@panel_staff_required
@require_POST
def categoria_crear_ajax(request):
    datos = request.POST.copy()
    if datos.get("orden", "") == "":
        datos["orden"] = 0
    form = CategoriaForm(datos)
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "message": "No se pudo crear la categoría. Revisa los datos ingresados.", "errors": form.errors.get_json_data()},
            status=400,
        )
    categoria = form.save()
    return JsonResponse({"ok": True, "id": categoria.id, "nombre": str(categoria)})


@panel_staff_required
def categoria_editar(request, categoria_id):
    categoria = get_object_or_404(Categoria, pk=categoria_id)
    form = CategoriaForm(request.POST or None, instance=categoria)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Categoría actualizada.")
        return redirect("panel_categorias")
    return render(request, "tienda/panel/categoria_form.html", {"form": form, "categoria": categoria, "titulo": "Editar categoría"})


@panel_staff_required
@require_POST
def categoria_toggle(request, categoria_id):
    categoria = get_object_or_404(Categoria, pk=categoria_id)
    categoria.activa = not categoria.activa
    categoria.save(update_fields=["activa"])
    estado = "activada" if categoria.activa else "inactivada"
    messages.success(request, f"Categoría {estado}.")
    return redirect("panel_categorias")


@panel_staff_required
def clientes_lista(request):
    q = request.GET.get("q", "").strip()
    tipo = request.GET.get("tipo", "").strip()
    activo = request.GET.get("activo", "").strip()
    ciudad = request.GET.get("ciudad", "").strip()
    clientes = Cliente.objects.annotate(
        num_proyectos=Count("proyectos", distinct=True),
    )
    if q:
        clientes = clientes.filter(
            Q(nombre__icontains=q)
            | Q(razon_social__icontains=q)
            | Q(identificacion__icontains=q)
            | Q(email__icontains=q)
            | Q(telefono__icontains=q)
            | Q(whatsapp__icontains=q)
            | Q(ciudad__icontains=q)
        )
    if tipo:
        clientes = clientes.filter(tipo_cliente=tipo)
    if activo in ["1", "0"]:
        clientes = clientes.filter(activo=activo == "1")
    if ciudad:
        clientes = clientes.filter(ciudad__icontains=ciudad)
    clientes = list(clientes)
    cliente_ids = [cliente.id for cliente in clientes]
    solicitudes_por_cliente = {cliente.id: set() for cliente in clientes}
    if cliente_ids:
        for cliente_id, solicitud_id in Solicitud.objects.filter(cliente_id__in=cliente_ids).values_list("cliente_id", "id"):
            solicitudes_por_cliente.setdefault(cliente_id, set()).add(solicitud_id)
        for cliente_id, solicitud_id in Solicitud.objects.filter(proyecto__cliente_id__in=cliente_ids).values_list("proyecto__cliente_id", "id"):
            solicitudes_por_cliente.setdefault(cliente_id, set()).add(solicitud_id)
    for cliente in clientes:
        cliente.num_solicitudes = len(solicitudes_por_cliente.get(cliente.id, set()))
    ciudades = Cliente.objects.exclude(ciudad="").values_list("ciudad", flat=True).distinct().order_by("ciudad")
    return render(
        request,
        "tienda/panel/clientes.html",
        {
            "clientes": clientes,
            "q": q,
            "tipo": tipo,
            "activo": activo,
            "ciudad": ciudad,
            "ciudades": ciudades,
            "tipos": Cliente.TIPO_CHOICES,
        },
    )


@panel_staff_required
def cliente_crear(request):
    form = ClienteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        cliente = form.save(commit=False)
        cliente.creado_por = request.user
        cliente.save()
        # La disponibilidad de Alegra no bloquea el alta local. La cola solo
        # registra una operación durable; nunca realiza HTTP desde el formulario.
        enqueue_create(cliente, actor=request.user)
        messages.success(request, "Cliente creado.")
        return redirect("panel_cliente_detalle", cliente_id=cliente.id)
    if request.method == "POST":
        messages.error(request, "No se creó el cliente. Revisa los errores marcados en el formulario.")
    return render(request, "tienda/panel/cliente_form.html", {"form": form, "titulo": "Crear cliente"})


@panel_staff_required
def cliente_editar(request, cliente_id):
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    form = ClienteForm(request.POST or None, instance=cliente)
    if request.method == "POST" and form.is_valid():
        form.save()
        # El guardado local no depende de Alegra. Si existe un vínculo y un
        # baseline válido, se prepara una única operación idempotente; nunca
        # se ejecuta una escritura externa desde el formulario.
        enqueue_update_if_changed(cliente, actor=request.user)
        messages.success(request, "Cliente actualizado.")
        return redirect("panel_cliente_detalle", cliente_id=cliente.id)
    return render(request, "tienda/panel/cliente_form.html", {"form": form, "cliente": cliente, "titulo": "Editar cliente"})


@panel_staff_required
@require_POST
def cliente_alegra_preparar(request, cliente_id):
    """Valida y registra una operación local; nunca ejecuta un POST externo."""
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    system = ExternalSystem.objects.filter(code="alegra", status=ExternalSystem.STATUS_ACTIVE).first()
    if not system:
        messages.error(request, "Alegra no está configurado localmente.")
        return redirect("panel_cliente_detalle", cliente_id=cliente.pk)
    try:
        transport = AlegraWriteClient()
        candidates = transport.find_candidates(identification=cliente.identificacion, name=cliente.nombre)
        if candidates:
            messages.error(request, "La preparación fue bloqueada: Alegra devolvió candidatos que requieren revisión manual.")
        else:
            operation, _ = AlegraContactWriteService(transport=transport).prepare_create(cliente, system)
            messages.success(request, f"Sincronización preparada localmente (operación {operation.pk}). No se escribió en Alegra.")
    except (AlegraError, ValidationError) as exc:
        messages.error(request, f"No se pudo preparar la sincronización: {exc}")
    return redirect("panel_cliente_detalle", cliente_id=cliente.pk)


@panel_staff_required
@require_POST
def cliente_alegra_preparar_actualizacion(request, cliente_id):
    """Consulta Alegra y prepara un PUT local; nunca lo ejecuta."""
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    mapping = ExternalObjectMap.objects.filter(
        resource_type="contacts", content_type__model="cliente", object_id=cliente.pk,
        status=ExternalObjectMap.STATUS_ACTIVE,
    ).select_related("system").first()
    if not mapping:
        messages.error(request, "No se puede preparar una actualización: el cliente no está vinculado con Alegra.")
        return redirect("panel_cliente_detalle", cliente_id=cliente.pk)
    try:
        transport = AlegraWriteClient()
        remote = transport.get_contact(mapping.external_id)
        baseline = (mapping.metadata or {}).get("last_synced_fields")
        if not isinstance(baseline, dict) or not baseline:
            raise ValidationError("No existe una línea base confirmada para detectar conflictos; requiere conciliación inicial.")
        operation, payload = AlegraContactWriteService(transport=transport).prepare_update(
            cliente, mapping.system, external_id=mapping.external_id, remote_row=remote, baseline=baseline,
        )
        messages.success(request, f"Actualización preparada localmente (operación {operation.pk}); campos: {', '.join(payload.keys())}. No se ejecutó PUT.")
    except (AlegraError, ValidationError) as exc:
        messages.error(request, f"No se pudo preparar la actualización: {exc}")
    return redirect("panel_cliente_detalle", cliente_id=cliente.pk)


@panel_staff_required
@require_GET
def cliente_alegra_revisar_cambios(request, cliente_id):
    """Consulta cambios externos y muestra una revisión firmada, sin persistirlos."""
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    mapping = ExternalObjectMap.objects.filter(
        resource_type="contacts", content_type__model="cliente", object_id=cliente.pk,
        status=ExternalObjectMap.STATUS_ACTIVE,
    ).select_related("system").first()
    if not mapping:
        messages.error(request, "No se puede consultar Alegra: el cliente no está vinculado.")
        return redirect("panel_cliente_detalle", cliente_id=cliente.pk)
    try:
        remote = AlegraWriteClient().get_contact(mapping.external_id)
        review = InboundClientSyncService(AlegraWriteClient()).review(cliente, mapping, remote)
        review_token = signing.dumps(review, salt="tienda.alegra_inbound_review")
    except (AlegraError, InboundSyncConflict, ValidationError) as exc:
        messages.error(request, f"No se pudieron consultar cambios de Alegra: {exc}")
        return redirect("panel_cliente_detalle", cliente_id=cliente.pk)
    return render(request, "tienda/panel/cliente_alegra_cambios.html", {
        "cliente": cliente,
        "alegra_map": mapping,
        "review": review,
        "review_token": review_token,
    })


@panel_staff_required
@require_POST
def cliente_alegra_aplicar_cambios(request, cliente_id):
    """Revalida y aplica exclusivamente campos Alegra aprobados por el usuario."""
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    token = request.POST.get("review_token", "")
    try:
        review = signing.loads(token, salt="tienda.alegra_inbound_review", max_age=900)
        if int(review.get("local_id")) != cliente.pk:
            raise InboundSyncConflict("La revisión no corresponde al cliente solicitado.")
        mapping_id = int(review.get("mapping_id"))
        approved = request.POST.getlist("approved_fields")
        result = InboundClientSyncService(AlegraWriteClient()).apply(
            cliente.pk, mapping_id, review, approved, actor=request.user,
        )
        if result["status"] == "noop":
            messages.info(request, "No hay cambios externos pendientes; no se modificó el cliente.")
        else:
            messages.success(request, f"Cambios de Alegra aplicados localmente: {', '.join(result['updated_fields'])}.")
    except (AlegraError, InboundSyncConflict, ValidationError, TypeError, ValueError) as exc:
        messages.error(request, f"No se aplicaron cambios de Alegra: {exc}")
    return redirect("panel_cliente_detalle", cliente_id=cliente.pk)


@panel_staff_required
@require_POST
def cliente_toggle(request, cliente_id):
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    cliente.activo = not cliente.activo
    cliente.save(update_fields=["activo", "fecha_actualizacion"])
    estado = "reactivado" if cliente.activo else "inactivado"
    messages.success(request, f"Cliente {estado}.")
    return redirect("panel_clientes")


@panel_staff_required
def cliente_detalle(request, cliente_id):
    cliente = get_object_or_404(Cliente.objects.select_related("creado_por"), pk=cliente_id)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "asociar_solicitud":
            solicitud = get_object_or_404(Solicitud, pk=request.POST.get("solicitud_id"))
            cliente_anterior = solicitud.cliente
            solicitud.cliente = cliente
            solicitud.save(update_fields=["cliente", "actualizado"])
            if cliente_anterior != cliente:
                crear_novedad(
                    solicitud,
                    request.user,
                    SolicitudNovedad.TIPO_SISTEMA,
                    f"Solicitud asociada al cliente {cliente}.",
                )
            messages.success(request, "Solicitud asociada al cliente.")
            return redirect("panel_cliente_detalle", cliente_id=cliente.id)

    proyectos = cliente.proyectos.select_related("responsable__user").order_by("-fecha_creacion")
    solicitudes = (
        Solicitud.objects.filter(Q(cliente=cliente) | Q(proyecto__cliente=cliente))
        .select_related("producto__categoria", "proyecto")
        .distinct()
        .order_by("-creado")
    )
    cotizaciones = cliente.cotizaciones.select_related("proyecto", "solicitud").order_by("-fecha_creacion")
    tareas = SolicitudTarea.objects.filter(
        Q(solicitud__cliente=cliente) | Q(solicitud__proyecto__cliente=cliente) | Q(proyecto__cliente=cliente),
        activa=True,
    ).select_related("solicitud__producto", "proyecto", "responsable__user").distinct().order_by("estado", "fecha_limite", "orden")
    novedades = SolicitudNovedad.objects.filter(
        Q(solicitud__cliente=cliente) | Q(solicitud__proyecto__cliente=cliente) | Q(proyecto__cliente=cliente)
    ).select_related("solicitud", "usuario", "tarea")[:12]
    archivos = SolicitudRespuesta.objects.filter(
        Q(solicitud__cliente=cliente) | Q(solicitud__proyecto__cliente=cliente),
        archivo__isnull=False,
    ).exclude(archivo="").select_related("solicitud")[:12]
    disponibles = Solicitud.objects.filter(cliente__isnull=True).select_related("producto").order_by("-creado")[:30]
    metricas = {
        "proyectos_activos": proyectos.filter(activo=True).count(),
        "proyectos_terminados": proyectos.filter(estado__in=[Proyecto.ESTADO_TERMINADO, Proyecto.ESTADO_ENTREGADO]).count(),
        "solicitudes": solicitudes.count(),
        "solicitudes_produccion": solicitudes.filter(estado_produccion__in=[Solicitud.PROD_ASIGNADO, Solicitud.PROD_EN_PROCESO, Solicitud.PROD_CON_NOVEDAD, Solicitud.PROD_CALIDAD]).count(),
        "tareas_pendientes": tareas.exclude(estado__in=[SolicitudTarea.ESTADO_TERMINADA, SolicitudTarea.ESTADO_APROBADA, SolicitudTarea.ESTADO_CANCELADA]).count(),
        "ultima_actividad": novedades.first().fecha_creacion if novedades else None,
        "cotizaciones": cotizaciones.count(),
        "cotizaciones_aprobadas": cotizaciones.filter(estado=Cotizacion.ESTADO_APROBADA).count(),
        "total_cotizado": sum((cotizacion.total for cotizacion in cotizaciones), Decimal("0")),
    }
    return render(
        request,
        "tienda/panel/cliente_detalle.html",
        {
            "cliente": cliente,
            "alegra_map": ExternalObjectMap.objects.filter(resource_type="contacts", content_type__model="cliente", object_id=cliente.id, status=ExternalObjectMap.STATUS_ACTIVE).select_related("system").first(),
            "alegra_operations": AlegraWriteOperation.objects.filter(client=cliente).order_by("-created_at", "-id")[:5],
            "contactos": cliente.contactos.all(),
            "puntos_venta": cliente.puntos_venta.all(),
            "usuarios_portal": cliente.usuarios_portal.select_related("user", "contacto"),
            "proyectos": proyectos,
            "solicitudes": solicitudes,
            "cotizaciones": cotizaciones,
            "tareas": tareas,
            "novedades": novedades,
            "archivos": archivos,
            "solicitudes_disponibles": disponibles,
            "metricas": metricas,
        },
    )


@panel_staff_required
def cliente_punto_venta_crear(request, cliente_id):
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    form = ClientePuntoVentaForm(request.POST or None, cliente=cliente)
    if request.method == "POST" and form.is_valid():
        punto = form.save(commit=False)
        punto.cliente = cliente
        punto.save()
        messages.success(request, "Punto de venta creado.")
        return redirect("panel_cliente_detalle", cliente_id=cliente.id)
    return render(
        request,
        "tienda/panel/cliente_punto_venta_form.html",
        {"form": form, "cliente": cliente, "titulo": "Nuevo punto de venta"},
    )


@panel_staff_required
def cliente_punto_venta_editar(request, cliente_id, punto_venta_id):
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    punto = get_object_or_404(ClientePuntoVenta, pk=punto_venta_id, cliente_id=cliente.id)
    form = ClientePuntoVentaForm(request.POST or None, instance=punto, cliente=cliente)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Punto de venta actualizado.")
        return redirect("panel_cliente_detalle", cliente_id=cliente.id)
    return render(
        request,
        "tienda/panel/cliente_punto_venta_form.html",
        {"form": form, "cliente": cliente, "punto_venta": punto, "titulo": "Editar punto de venta"},
    )


@panel_staff_required
@require_POST
def cliente_punto_venta_toggle(request, cliente_id, punto_venta_id):
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    punto = get_object_or_404(ClientePuntoVenta, pk=punto_venta_id, cliente_id=cliente.id)
    punto.activo = not punto.activo
    punto.save(update_fields=["activo", "fecha_actualizacion"])
    messages.success(request, f"Punto de venta {'activado' if punto.activo else 'desactivado'}.")
    return redirect("panel_cliente_detalle", cliente_id=cliente.id)


@panel_staff_required
def cliente_portal_crear(request, cliente_id):
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    form = ClienteUsuarioPortalForm(request.POST or None, cliente=cliente)
    if request.method == "POST" and form.is_valid():
        cliente_usuario = form.save()
        enviar_confirmacion_registro_cliente(cliente_usuario)
        messages.success(request, "Usuario de portal creado.")
        return redirect("panel_cliente_detalle", cliente_id=cliente.id)
    return render(
        request,
        "tienda/panel/cliente_usuario_form.html",
        {"form": form, "cliente": cliente, "titulo": "Crear acceso portal"},
    )


@panel_staff_required
def cliente_portal_editar(request, usuario_id):
    cliente_usuario = get_object_or_404(ClienteUsuario.objects.select_related("cliente", "user", "contacto"), pk=usuario_id)
    form = ClienteUsuarioPortalForm(request.POST or None, cliente=cliente_usuario.cliente, instance=cliente_usuario)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Usuario de portal actualizado.")
        return redirect("panel_cliente_detalle", cliente_id=cliente_usuario.cliente_id)
    return render(
        request,
        "tienda/panel/cliente_usuario_form.html",
        {"form": form, "cliente": cliente_usuario.cliente, "cliente_usuario": cliente_usuario, "titulo": "Editar acceso portal"},
    )


@panel_staff_required
@require_POST
def cliente_portal_toggle(request, usuario_id):
    cliente_usuario = get_object_or_404(ClienteUsuario.objects.select_related("cliente"), pk=usuario_id)
    cliente_usuario.activo = not cliente_usuario.activo
    cliente_usuario.save(update_fields=["activo", "fecha_actualizacion"])
    estado = "activado" if cliente_usuario.activo else "inactivado"
    messages.success(request, f"Usuario de portal {estado}.")
    return redirect("panel_cliente_detalle", cliente_id=cliente_usuario.cliente_id)


@panel_staff_required
@require_POST
def cliente_portal_password_reset(request, usuario_id):
    cliente_usuario = get_object_or_404(ClienteUsuario.objects.select_related("cliente", "user"), pk=usuario_id)
    email = cliente_usuario.user.email or cliente_usuario.user.username
    form = ClientePasswordResetForm({"email": email})
    if form.is_valid():
        form.save(
            request=request,
            use_https=request.is_secure(),
            email_template_name="tienda/emails/password_reset_cliente.txt",
            html_email_template_name="tienda/emails/password_reset_cliente.html",
            subject_template_name="tienda/emails/password_reset_cliente_subject.txt",
            from_email=remitente_betta(),
            extra_email_context={
                "site_url": getattr(settings, "SITE_URL", "").rstrip("/"),
                "from_name": getattr(settings, "BETTA_EMAIL_FROM_NAME", "Betta Diseño"),
                "email_logo_url": logo_email_url(),
            },
        )
        messages.success(request, "Correo de recuperación enviado o preparado según la configuración de email.")
    else:
        messages.error(request, "No se pudo preparar la recuperación de contraseña para este usuario.")
    return redirect("panel_cliente_detalle", cliente_id=cliente_usuario.cliente_id)


@panel_staff_required
def cliente_contacto_crear(request, cliente_id):
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    form = ClienteContactoForm(request.POST or None, cliente=cliente)
    if request.method == "POST" and form.is_valid():
        contacto = form.save(commit=False)
        contacto.cliente = cliente
        contacto.save()
        messages.success(request, "Contacto creado.")
        return redirect("panel_cliente_detalle", cliente_id=cliente.id)
    return render(request, "tienda/panel/cliente_contacto_form.html", {"form": form, "cliente": cliente, "titulo": "Crear contacto"})


@panel_staff_required
def cliente_contacto_editar(request, contacto_id):
    contacto = get_object_or_404(ClienteContacto.objects.select_related("cliente"), pk=contacto_id)
    form = ClienteContactoForm(request.POST or None, instance=contacto)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Contacto actualizado.")
        return redirect("panel_cliente_detalle", cliente_id=contacto.cliente_id)
    return render(request, "tienda/panel/cliente_contacto_form.html", {"form": form, "cliente": contacto.cliente, "contacto": contacto, "titulo": "Editar contacto"})


@panel_staff_required
@require_POST
def cliente_contacto_toggle(request, contacto_id):
    contacto = get_object_or_404(ClienteContacto.objects.select_related("cliente"), pk=contacto_id)
    contacto.activo = not contacto.activo
    contacto.save(update_fields=["activo", "fecha_actualizacion"])
    estado = "reactivado" if contacto.activo else "inactivado"
    messages.success(request, f"Contacto {estado}.")
    return redirect("panel_cliente_detalle", cliente_id=contacto.cliente_id)


@panel_staff_required
def proyectos_lista(request):
    q = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "").strip()
    prioridad = request.GET.get("prioridad", "").strip()
    activo = request.GET.get("activo", "").strip()
    cliente_id = request.GET.get("cliente", "").strip()
    proyectos = Proyecto.objects.select_related("cliente", "contacto", "responsable__user").annotate(
        num_solicitudes=Count("solicitudes", distinct=True),
        num_tareas_solicitudes=Count("solicitudes__tareas", filter=Q(solicitudes__tareas__activa=True), distinct=True),
        num_tareas_directas=Count("tareas", filter=Q(tareas__activa=True), distinct=True),
    )
    if q:
        proyectos = proyectos.filter(
            Q(nombre__icontains=q)
            | Q(cliente_nombre__icontains=q)
            | Q(cliente_contacto__icontains=q)
            | Q(contacto__nombre__icontains=q)
            | Q(contacto__email__icontains=q)
            | Q(cliente__nombre__icontains=q)
            | Q(cliente__razon_social__icontains=q)
            | Q(cliente__identificacion__icontains=q)
            | Q(responsable__user__username__icontains=q)
            | Q(responsable__user__first_name__icontains=q)
            | Q(responsable__user__last_name__icontains=q)
        )
    if estado:
        proyectos = proyectos.filter(estado=estado)
    if prioridad:
        proyectos = proyectos.filter(prioridad=prioridad)
    if activo in ["1", "0"]:
        proyectos = proyectos.filter(activo=activo == "1")
    if cliente_id.isdigit():
        proyectos = proyectos.filter(cliente_id=cliente_id)
    proyectos = list(proyectos)
    for proyecto in proyectos:
        proyecto.num_tareas = proyecto.num_tareas_solicitudes + proyecto.num_tareas_directas
    return render(
        request,
        "tienda/panel/proyectos.html",
        {
            "proyectos": proyectos,
            "q": q,
            "estado": estado,
            "prioridad": prioridad,
            "activo": activo,
            "cliente_id": cliente_id,
            "clientes": Cliente.objects.filter(activo=True).order_by("nombre", "razon_social"),
            "estados": Proyecto.ESTADOS,
            "prioridades": Proyecto.PRIORIDADES,
        },
    )


@panel_staff_required
def proyecto_crear(request):
    initial = {}
    cliente_id = request.GET.get("cliente", "").strip()
    if cliente_id.isdigit():
        initial["cliente"] = cliente_id
        contacto = cliente_contacto_principal(Cliente.objects.filter(pk=cliente_id).first())
        if contacto:
            initial["contacto"] = contacto.id
    form = ProyectoForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        proyecto = form.save(commit=False)
        proyecto.creado_por = request.user
        proyecto.save()
        messages.success(request, "Proyecto creado.")
        return redirect("panel_proyecto_detalle", proyecto_id=proyecto.id)
    return render(request, "tienda/panel/proyecto_form.html", {"form": form, "titulo": "Crear proyecto"})


@panel_staff_required
def proyecto_editar(request, proyecto_id):
    proyecto = get_object_or_404(Proyecto, pk=proyecto_id)
    cliente_anterior = proyecto.cliente
    contacto_anterior = proyecto.contacto
    estado_anterior = proyecto.estado
    form = ProyectoForm(request.POST or None, instance=proyecto)
    if request.method == "POST" and form.is_valid():
        proyecto = form.save()
        if cliente_anterior != proyecto.cliente or contacto_anterior != proyecto.contacto:
            for solicitud in proyecto.solicitudes.all():
                update_fields = []
                if proyecto.cliente and not solicitud.cliente_id:
                    solicitud.cliente = proyecto.cliente
                    update_fields.append("cliente")
                if proyecto.contacto_id and (not solicitud.contacto_id or solicitud.contacto_id == getattr(contacto_anterior, "id", None)):
                    solicitud.contacto = proyecto.contacto
                    update_fields.append("contacto")
                elif not proyecto.contacto_id and contacto_anterior and solicitud.contacto_id == contacto_anterior.id:
                    solicitud.contacto = None
                    update_fields.append("contacto")
                if update_fields:
                    update_fields.append("actualizado")
                    solicitud.save(update_fields=update_fields)
                crear_novedad(
                    solicitud,
                    request.user,
                    SolicitudNovedad.TIPO_SISTEMA,
                    f"Cliente/contacto del proyecto actualizado a {proyecto.cliente or 'sin cliente'}.",
                )
        if estado_anterior != proyecto.estado:
            notificar_clientes_proyecto(
                proyecto,
                f"Proyecto {proyecto.nombre}: {proyecto.get_estado_display()}",
                "El estado de tu proyecto fue actualizado.",
                tipo=NotificacionCliente.TIPO_PROYECTO,
            )
        messages.success(request, "Proyecto actualizado.")
        return redirect("panel_proyecto_detalle", proyecto_id=proyecto.id)
    return render(request, "tienda/panel/proyecto_form.html", {"form": form, "proyecto": proyecto, "titulo": "Editar proyecto"})


@panel_staff_required
@require_POST
def proyecto_toggle(request, proyecto_id):
    proyecto = get_object_or_404(Proyecto, pk=proyecto_id)
    proyecto.activo = not proyecto.activo
    proyecto.save(update_fields=["activo", "fecha_actualizacion"])
    estado = "reactivado" if proyecto.activo else "inactivado"
    messages.success(request, f"Proyecto {estado}.")
    return redirect("panel_proyectos")


@panel_staff_required
def proyecto_detalle(request, proyecto_id):
    proyecto = get_object_or_404(Proyecto.objects.select_related("cliente", "contacto", "responsable__user", "creado_por").prefetch_related("puntos_venta"), pk=proyecto_id)
    asociar_form = ProyectoSolicitudForm(proyecto=proyecto)
    tarea_form = SolicitudTareaForm(proyecto=proyecto)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "asociar_solicitudes":
            asociar_form = ProyectoSolicitudForm(request.POST, proyecto=proyecto)
            if asociar_form.is_valid():
                asociadas = 0
                for solicitud in asociar_form.cleaned_data["solicitudes"]:
                    solicitud.proyecto = proyecto
                    update_fields = ["proyecto", "actualizado"]
                    if proyecto.cliente_id and not solicitud.cliente_id:
                        solicitud.cliente = proyecto.cliente
                        update_fields.append("cliente")
                    if proyecto.contacto_id and not solicitud.contacto_id:
                        solicitud.contacto = proyecto.contacto
                        update_fields.append("contacto")
                    solicitud.save(update_fields=update_fields)
                    crear_novedad(
                        solicitud,
                        request.user,
                        SolicitudNovedad.TIPO_SISTEMA,
                        f"Asociada al proyecto {proyecto.nombre}.",
                    )
                    asociadas += 1
                messages.success(request, f"Solicitudes asociadas: {asociadas}.")
                return redirect("panel_proyecto_detalle", proyecto_id=proyecto.id)
        elif action == "quitar_solicitud":
            solicitud = get_object_or_404(Solicitud, pk=request.POST.get("solicitud_id"), proyecto=proyecto)
            solicitud.proyecto = None
            solicitud.save(update_fields=["proyecto", "actualizado"])
            crear_novedad(
                solicitud,
                request.user,
                SolicitudNovedad.TIPO_SISTEMA,
                f"Solicitud retirada del proyecto {proyecto.nombre}.",
            )
            messages.success(request, "Solicitud retirada del proyecto.")
            return redirect("panel_proyecto_detalle", proyecto_id=proyecto.id)
        elif action == "crear_tarea_proyecto":
            tarea_form = SolicitudTareaForm(request.POST, request.FILES, proyecto=proyecto)
            if tarea_form.is_valid():
                tarea = tarea_form.save(commit=False)
                tarea.proyecto = proyecto
                tarea.solicitud = None
                tarea.asignado_por = request.user
                if tarea.estado in [SolicitudTarea.ESTADO_TERMINADA, SolicitudTarea.ESTADO_APROBADA] and not tarea.fecha_finalizacion:
                    tarea.fecha_finalizacion = timezone.now()
                    tarea.finalizada_por = request.user
                tarea.save()
                crear_novedad(
                    None,
                    request.user,
                    SolicitudNovedad.TIPO_TAREA_CREADA,
                    f"Tarea general de proyecto creada: {tarea.titulo}.",
                    tarea=tarea,
                    proyecto=proyecto,
                    archivo_evidencia=tarea.evidencia_archivo if tarea.evidencia_archivo else None,
                )
                if tarea.responsable_id:
                    crear_novedad(
                        None,
                        request.user,
                        SolicitudNovedad.TIPO_TAREA_ASIGNADA,
                        f"Tarea '{tarea.titulo}' asignada a {tarea.responsable}.",
                        tarea=tarea,
                        proyecto=proyecto,
                    )
                    crear_notificacion(
                        tarea.responsable.user,
                        None,
                        f"Nueva tarea #{tarea.id}",
                        f"Te asignaron la tarea: {tarea.titulo}.",
                        tipo=Notificacion.TIPO_TAREA,
                        tarea=tarea,
                        proyecto=proyecto,
                    )
                messages.success(request, "Tarea de proyecto creada.")
                return redirect("panel_proyecto_detalle", proyecto_id=proyecto.id)

    solicitudes = proyecto.solicitudes.select_related("producto__categoria", "cliente", "contacto", "punto_venta").prefetch_related("tareas").order_by("-creado")
    cotizaciones = proyecto.cotizaciones.select_related("cliente", "contacto", "solicitud", "punto_venta").order_by("-fecha_creacion")
    tareas = SolicitudTarea.objects.filter(Q(solicitud__proyecto=proyecto) | Q(proyecto=proyecto)).select_related(
        "solicitud__producto",
        "proyecto",
        "responsable__user",
    ).distinct()
    tareas_activas = tareas.filter(activa=True)
    novedades = SolicitudNovedad.objects.filter(Q(solicitud__proyecto=proyecto) | Q(proyecto=proyecto)).select_related("usuario", "tarea")[:12]
    metricas = {
        "solicitudes": solicitudes.count(),
        "tareas": tareas_activas.count(),
        "pendientes": tareas_activas.filter(estado__in=[SolicitudTarea.ESTADO_PENDIENTE, SolicitudTarea.ESTADO_ASIGNADA]).count(),
        "en_proceso": tareas_activas.filter(estado=SolicitudTarea.ESTADO_EN_PROCESO).count(),
        "terminadas": tareas_activas.filter(estado__in=[SolicitudTarea.ESTADO_TERMINADA, SolicitudTarea.ESTADO_APROBADA]).count(),
        "avance": proyecto.avance_porcentaje,
    }
    return render(
        request,
        "tienda/panel/proyecto_detalle.html",
        {
            "proyecto": proyecto,
            "asociar_form": asociar_form,
            "tarea_form": tarea_form,
            "solicitudes": solicitudes,
            "cotizaciones": cotizaciones,
            "tareas": tareas.order_by("estado", "fecha_limite", "orden"),
            "novedades": novedades,
            "metricas": metricas,
        },
    )


@panel_staff_required
def solicitud_detalle(request, pk):
    solicitud = get_object_or_404(Solicitud.objects.select_related("producto__categoria", "cliente", "contacto", "proyecto__cliente", "proyecto__contacto"), pk=pk)
    form = SolicitudEstadoForm(instance=solicitud)
    asignacion_form = SolicitudAsignacionForm()
    estado_produccion_form = AdminProduccionEstadoForm(initial={"estado_produccion": solicitud.estado_produccion})
    novedad_form = SolicitudNovedadForm()
    proyecto_form = SolicitudProyectoForm(instance=solicitud)
    cliente_form = SolicitudClienteForm(instance=solicitud)
    tarea_form = SolicitudTareaForm(solicitud=solicitud)

    if request.method == "POST":
        action = request.POST.get("action", "general")

        if action == "general":
            form = SolicitudEstadoForm(request.POST, instance=solicitud)
            if form.is_valid():
                estado_anterior = solicitud.estado
                facturacion_anterior = (
                    solicitud.valor_facturado,
                    solicitud.estado_facturacion,
                    solicitud.numero_factura,
                    solicitud.fecha_factura,
                    solicitud.precio_final,
                )
                form.save()
                facturacion_nueva = (
                    solicitud.valor_facturado,
                    solicitud.estado_facturacion,
                    solicitud.numero_factura,
                    solicitud.fecha_factura,
                    solicitud.precio_final,
                )
                if estado_anterior != solicitud.estado:
                    notificar_clientes_solicitud(
                        solicitud,
                        f"Pedido OP-{solicitud.id:06d}: {solicitud.get_estado_display()}",
                        "El estado general de tu pedido fue actualizado.",
                        tipo=NotificacionCliente.TIPO_PEDIDO,
                    )
                if facturacion_anterior != facturacion_nueva:
                    notificar_clientes_solicitud(
                        solicitud,
                        f"Facturación OP-{solicitud.id:06d}",
                        "La información comercial visible de tu pedido fue actualizada.",
                        tipo=NotificacionCliente.TIPO_FACTURACION,
                    )
                messages.success(request, "Solicitud actualizada correctamente.")
                return redirect("panel_solicitud_detalle", pk=solicitud.pk)
        elif action == "estado_produccion":
            estado_produccion_form = AdminProduccionEstadoForm(request.POST)
            if estado_produccion_form.is_valid():
                cambiar_estado_produccion(
                    solicitud,
                    estado_produccion_form.cleaned_data["estado_produccion"],
                    request.user,
                    estado_produccion_form.cleaned_data.get("comentario", ""),
                )
                messages.success(request, "Estado de producción actualizado.")
                return redirect("panel_solicitud_detalle", pk=solicitud.pk)
        elif action == "proyecto":
            proyecto_anterior = solicitud.proyecto
            proyecto_form = SolicitudProyectoForm(request.POST, instance=solicitud)
            if proyecto_form.is_valid():
                solicitud = proyecto_form.save()
                if proyecto_anterior != solicitud.proyecto:
                    update_fields = []
                    if solicitud.proyecto_id:
                        if solicitud.proyecto.cliente_id and not solicitud.cliente_id:
                            solicitud.cliente = solicitud.proyecto.cliente
                            update_fields.append("cliente")
                        if solicitud.proyecto.contacto_id and not solicitud.contacto_id:
                            solicitud.contacto = solicitud.proyecto.contacto
                            update_fields.append("contacto")
                    if update_fields:
                        update_fields.append("actualizado")
                        solicitud.save(update_fields=update_fields)
                    if solicitud.proyecto:
                        comentario = f"Asociada al proyecto {solicitud.proyecto.nombre}."
                    elif proyecto_anterior:
                        comentario = f"Solicitud retirada del proyecto {proyecto_anterior.nombre}."
                    else:
                        comentario = "Proyecto actualizado."
                    crear_novedad(solicitud, request.user, SolicitudNovedad.TIPO_SISTEMA, comentario)
                messages.success(request, "Proyecto de la solicitud actualizado.")
                return redirect("panel_solicitud_detalle", pk=solicitud.pk)
        elif action == "cliente":
            cliente_anterior = solicitud.cliente
            contacto_anterior = solicitud.contacto
            cliente_form = SolicitudClienteForm(request.POST, instance=solicitud)
            if cliente_form.is_valid():
                solicitud = cliente_form.save()
                if cliente_anterior != solicitud.cliente or contacto_anterior != solicitud.contacto:
                    if solicitud.cliente:
                        comentario = f"Solicitud asociada al cliente {solicitud.cliente}."
                        if solicitud.contacto:
                            comentario += f" Contacto: {solicitud.contacto.nombre}."
                    elif cliente_anterior:
                        comentario = f"Solicitud retirada del cliente {cliente_anterior}."
                    else:
                        comentario = "Cliente/contacto actualizado."
                    crear_novedad(solicitud, request.user, SolicitudNovedad.TIPO_SISTEMA, comentario)
                    notificar_clientes_solicitud(
                        solicitud,
                        f"Pedido OP-{solicitud.id:06d} disponible",
                        "Este pedido quedo asociado a tu cuenta de cliente.",
                        tipo=NotificacionCliente.TIPO_PEDIDO,
                    )
                messages.success(request, "Cliente de la solicitud actualizado.")
                return redirect("panel_solicitud_detalle", pk=solicitud.pk)
        elif action == "crear_cliente_desde_solicitud":
            if solicitud.cliente_id:
                messages.info(request, "La solicitud ya tiene un cliente asociado.")
                return redirect("panel_solicitud_detalle", pk=solicitud.pk)
            cliente = None
            if solicitud.cliente_email:
                cliente = Cliente.objects.filter(email__iexact=solicitud.cliente_email).first()
            if cliente is None:
                cliente = Cliente.objects.create(
                    tipo_cliente=Cliente.TIPO_PERSONA,
                    nombre=solicitud.cliente_nombre,
                    email=solicitud.cliente_email,
                    telefono=solicitud.cliente_celular,
                    whatsapp=solicitud.cliente_celular,
                    creado_por=request.user,
                )
                mensaje = f"Cliente creado desde la solicitud y asociado: {cliente}."
            else:
                mensaje = f"Cliente existente asociado desde email: {cliente}."
            contacto = None
            if solicitud.cliente_email:
                contacto = cliente.contactos.filter(email__iexact=solicitud.cliente_email).first()
            if contacto is None and solicitud.cliente_nombre:
                contacto = ClienteContacto.objects.create(
                    cliente=cliente,
                    nombre=solicitud.cliente_nombre,
                    email=solicitud.cliente_email,
                    telefono=solicitud.cliente_celular,
                    whatsapp=solicitud.cliente_celular,
                    es_principal=not cliente.contactos.filter(activo=True, es_principal=True).exists(),
                )
            solicitud.cliente = cliente
            solicitud.contacto = contacto
            solicitud.save(update_fields=["cliente", "contacto", "actualizado"])
            crear_novedad(solicitud, request.user, SolicitudNovedad.TIPO_SISTEMA, mensaje)
            messages.success(request, mensaje)
            return redirect("panel_solicitud_detalle", pk=solicitud.pk)
        elif action == "asignar":
            asignacion_form = SolicitudAsignacionForm(request.POST)
            if asignacion_form.is_valid():
                empleados = asignacion_form.cleaned_data["empleados"]
                rol = asignacion_form.cleaned_data.get("rol_en_trabajo", "")
                observacion = asignacion_form.cleaned_data.get("observacion", "")
                nuevas = 0
                reactivadas = 0
                omitidas = 0
                with transaction.atomic():
                    for empleado in empleados:
                        existente = SolicitudAsignacion.objects.filter(solicitud=solicitud, empleado=empleado).order_by("-activa", "-fecha_asignacion").first()
                        if existente and existente.activa:
                            omitidas += 1
                            continue
                        if existente:
                            existente.activa = True
                            existente.fecha_asignacion = timezone.now()
                            existente.fecha_desasignacion = None
                            existente.desasignado_por = None
                            existente.asignado_por = request.user
                            existente.rol_en_trabajo = rol
                            existente.observacion = observacion
                            existente.save()
                            asignacion = existente
                            reactivadas += 1
                        else:
                            asignacion = SolicitudAsignacion.objects.create(
                                solicitud=solicitud,
                                empleado=empleado,
                                asignado_por=request.user,
                                rol_en_trabajo=rol,
                                observacion=observacion,
                            )
                            nuevas += 1
                        crear_novedad(
                            solicitud,
                            request.user,
                            SolicitudNovedad.TIPO_ASIGNACION,
                            f"Asignado a {empleado}. Rol: {rol or '-'}",
                        )
                        crear_notificacion(
                            empleado.user,
                            solicitud,
                            f"Nueva asignación #{solicitud.id}",
                            observacion or f"Te asignaron la solicitud #{solicitud.id}.",
                            tipo=Notificacion.TIPO_ASIGNACION,
                        )
                    if nuevas or reactivadas:
                        cambiar_estado_produccion(solicitud, Solicitud.PROD_ASIGNADO, request.user, "Solicitud asignada a producción.", notificar=False)
                messages.success(request, f"Asignaciones procesadas. Nuevas: {nuevas}. Reactivadas: {reactivadas}. Omitidas: {omitidas}.")
                return redirect("panel_solicitud_detalle", pk=solicitud.pk)
        elif action == "crear_tarea":
            tarea_form = SolicitudTareaForm(request.POST, request.FILES, solicitud=solicitud)
            if tarea_form.is_valid():
                tarea = tarea_form.save(commit=False)
                tarea.solicitud = solicitud
                tarea.proyecto = solicitud.proyecto
                tarea.asignado_por = request.user
                if tarea.estado in [SolicitudTarea.ESTADO_TERMINADA, SolicitudTarea.ESTADO_APROBADA] and not tarea.fecha_finalizacion:
                    tarea.fecha_finalizacion = timezone.now()
                    tarea.finalizada_por = request.user
                tarea.save()
                crear_novedad(
                    solicitud,
                    request.user,
                    SolicitudNovedad.TIPO_TAREA_CREADA,
                    f"Tarea creada: {tarea.titulo}.",
                    tarea=tarea,
                    archivo_evidencia=tarea.evidencia_archivo if tarea.evidencia_archivo else None,
                )
                if tarea.responsable_id:
                    crear_novedad(
                        solicitud,
                        request.user,
                        SolicitudNovedad.TIPO_TAREA_ASIGNADA,
                        f"Tarea '{tarea.titulo}' asignada a {tarea.responsable}.",
                        tarea=tarea,
                    )
                    crear_notificacion(
                        tarea.responsable.user,
                        solicitud,
                        f"Nueva tarea #{tarea.id}",
                        f"Te asignaron la tarea: {tarea.titulo}.",
                        tipo=Notificacion.TIPO_TAREA,
                        tarea=tarea,
                        proyecto=solicitud.proyecto,
                    )
                messages.success(request, "Tarea creada.")
                return redirect("panel_solicitud_detalle", pk=solicitud.pk)
        elif action in ["desasignar", "reactivar_asignacion"]:
            asignacion = get_object_or_404(SolicitudAsignacion.objects.select_related("solicitud", "empleado__user"), pk=request.POST.get("asignacion_id"), solicitud=solicitud)
            if action == "desasignar":
                if asignacion.activa:
                    asignacion.activa = False
                    asignacion.fecha_desasignacion = timezone.now()
                    asignacion.desasignado_por = request.user
                    asignacion.save(update_fields=["activa", "fecha_desasignacion", "desasignado_por"])
                    crear_novedad(solicitud, request.user, SolicitudNovedad.TIPO_DESASIGNACION, f"Desasignado {asignacion.empleado}.")
                    crear_notificacion(asignacion.empleado.user, solicitud, f"Desasignación #{solicitud.id}", "Ya no tienes este pedido asignado.", tipo=Notificacion.TIPO_DESASIGNACION)
                    if not solicitud.asignaciones.filter(activa=True).exists() and solicitud.estado_produccion in [Solicitud.PROD_ASIGNADO, Solicitud.PROD_EN_PROCESO, Solicitud.PROD_CON_NOVEDAD]:
                        cambiar_estado_produccion(solicitud, Solicitud.PROD_PENDIENTE_ASIGNAR, request.user, "Solicitud sin asignaciones activas.", notificar=False)
                    messages.success(request, "Asignación desactivada.")
            else:
                if not asignacion.activa:
                    asignacion.activa = True
                    asignacion.fecha_asignacion = timezone.now()
                    asignacion.fecha_desasignacion = None
                    asignacion.desasignado_por = None
                    asignacion.asignado_por = request.user
                    asignacion.save()
                    crear_novedad(solicitud, request.user, SolicitudNovedad.TIPO_ASIGNACION, f"Reactivada asignación de {asignacion.empleado}.")
                    crear_notificacion(asignacion.empleado.user, solicitud, f"Asignación reactivada #{solicitud.id}", "Tu asignación fue reactivada.", tipo=Notificacion.TIPO_ASIGNACION)
                    if solicitud.estado_produccion == Solicitud.PROD_PENDIENTE_ASIGNAR:
                        cambiar_estado_produccion(solicitud, Solicitud.PROD_ASIGNADO, request.user, "Solicitud reasignada a producción.", notificar=False)
                    messages.success(request, "Asignación reactivada.")
            return redirect("panel_solicitud_detalle", pk=solicitud.pk)
        elif action == "novedad":
            novedad_form = SolicitudNovedadForm(request.POST, request.FILES)
            if novedad_form.is_valid():
                novedad = novedad_form.save(commit=False)
                novedad.solicitud = solicitud
                novedad.usuario = request.user
                novedad.tipo = SolicitudNovedad.TIPO_EVIDENCIA if novedad.archivo_evidencia else SolicitudNovedad.TIPO_COMENTARIO
                novedad.save()
                notificar_asignados(solicitud, f"Novedad en solicitud #{solicitud.id}", novedad.comentario[:180], tipo=Notificacion.TIPO_NOVEDAD, exclude_user=request.user)
                if novedad.visible_para_cliente:
                    notificar_clientes_solicitud(
                        solicitud,
                        f"Novedad en OP-{solicitud.id:06d}",
                        novedad.comentario[:240],
                        tipo=NotificacionCliente.TIPO_NOVEDAD,
                    )
                messages.success(request, "Novedad registrada.")
                return redirect("panel_solicitud_detalle", pk=solicitud.pk)
    asignaciones = solicitud.asignaciones.select_related("empleado__user", "asignado_por", "desasignado_por").order_by("-activa", "-fecha_asignacion")
    tareas = solicitud.tareas.select_related("responsable__user", "asignado_por").order_by("orden", "fecha_limite", "id")
    novedades = solicitud.novedades.select_related("usuario", "tarea").order_by("-fecha_creacion", "-id")
    cotizaciones = solicitud.cotizaciones.select_related("cliente", "contacto", "proyecto").order_by("-fecha_creacion")
    cliente_sugerido = None
    contacto_sugerido = None
    if solicitud.proyecto_id and solicitud.proyecto.cliente_id and solicitud.cliente_id != solicitud.proyecto.cliente_id:
        cliente_sugerido = solicitud.proyecto.cliente
    if solicitud.proyecto_id and solicitud.proyecto.contacto_id and solicitud.contacto_id != solicitud.proyecto.contacto_id:
        contacto_sugerido = solicitud.proyecto.contacto
    return render(
        request,
        "tienda/panel/solicitud_detalle.html",
        {
            "solicitud": solicitud,
            "form": form,
            "asignacion_form": asignacion_form,
            "estado_produccion_form": estado_produccion_form,
            "novedad_form": novedad_form,
            "proyecto_form": proyecto_form,
            "cliente_form": cliente_form,
            "cliente_sugerido": cliente_sugerido,
            "contacto_sugerido": contacto_sugerido,
            "tarea_form": tarea_form,
            "asignaciones": asignaciones,
            "tareas": tareas,
            "novedades": novedades,
            "cotizaciones": cotizaciones,
        },
    )


@panel_staff_required
def tarea_editar(request, tarea_id):
    tarea = get_object_or_404(
        SolicitudTarea.objects.select_related("solicitud__producto", "proyecto", "responsable__user"),
        pk=tarea_id,
    )
    responsable_anterior = tarea.responsable_id
    estado_anterior = tarea.estado
    form = SolicitudTareaForm(request.POST or None, request.FILES or None, instance=tarea)
    if request.method == "POST" and form.is_valid():
        tarea = form.save(commit=False)
        if tarea.estado in [SolicitudTarea.ESTADO_TERMINADA, SolicitudTarea.ESTADO_APROBADA] and not tarea.fecha_finalizacion:
            tarea.fecha_finalizacion = timezone.now()
            tarea.finalizada_por = request.user
        tarea.save()
        if responsable_anterior != tarea.responsable_id and tarea.responsable_id:
            crear_novedad(
                tarea.solicitud,
                request.user,
                SolicitudNovedad.TIPO_TAREA_ASIGNADA,
                f"Tarea '{tarea.titulo}' asignada a {tarea.responsable}.",
                tarea=tarea,
                proyecto=tarea.proyecto,
            )
            crear_notificacion(
                tarea.responsable.user,
                tarea.solicitud,
                f"Tarea asignada #{tarea.id}",
                f"Te asignaron la tarea: {tarea.titulo}.",
                tipo=Notificacion.TIPO_TAREA,
                tarea=tarea,
                proyecto=tarea.proyecto or getattr(tarea.solicitud, "proyecto", None),
            )
        if estado_anterior != tarea.estado:
            crear_novedad(
                tarea.solicitud,
                request.user,
                SolicitudNovedad.TIPO_TAREA_ESTADO,
                f"Tarea '{tarea.titulo}' cambio de {estado_tarea_label(estado_anterior)} a {estado_tarea_label(tarea.estado)}.",
                estado_anterior=estado_anterior,
                estado_nuevo=tarea.estado,
                tarea=tarea,
                proyecto=tarea.proyecto,
                archivo_evidencia=tarea.evidencia_archivo if tarea.evidencia_archivo else None,
            )
            notificar_tarea(
                tarea,
                f"Tarea #{tarea.id}: {estado_tarea_label(tarea.estado)}",
                f"La tarea '{tarea.titulo}' cambió de estado.",
                tipo=Notificacion.TIPO_TAREA,
                exclude_user=request.user,
            )
        messages.success(request, "Tarea actualizada.")
        if tarea.solicitud_id:
            return redirect("panel_solicitud_detalle", pk=tarea.solicitud_id)
        return redirect("panel_proyecto_detalle", proyecto_id=tarea.proyecto_id)
    return render(request, "tienda/panel/tarea_form.html", {"form": form, "tarea": tarea, "titulo": "Editar tarea"})


@panel_staff_required
@require_POST
def tarea_toggle(request, tarea_id):
    tarea = get_object_or_404(SolicitudTarea.objects.select_related("solicitud", "proyecto", "responsable__user"), pk=tarea_id)
    if tarea.activa:
        tarea.activa = False
        tarea.estado = SolicitudTarea.ESTADO_CANCELADA
        tarea.save(update_fields=["activa", "estado", "fecha_actualizacion"])
        crear_novedad(
            tarea.solicitud,
            request.user,
            SolicitudNovedad.TIPO_TAREA_ESTADO,
            f"Tarea cancelada/inactivada: {tarea.titulo}.",
            estado_nuevo=SolicitudTarea.ESTADO_CANCELADA,
            tarea=tarea,
            proyecto=tarea.proyecto,
        )
        messages.success(request, "Tarea inactivada.")
    else:
        tarea.activa = True
        if tarea.estado == SolicitudTarea.ESTADO_CANCELADA:
            tarea.estado = SolicitudTarea.ESTADO_ASIGNADA if tarea.responsable_id else SolicitudTarea.ESTADO_PENDIENTE
        tarea.save(update_fields=["activa", "estado", "fecha_actualizacion"])
        crear_novedad(
            tarea.solicitud,
            request.user,
            SolicitudNovedad.TIPO_TAREA_ESTADO,
            f"Tarea reactivada: {tarea.titulo}.",
            estado_nuevo=tarea.estado,
            tarea=tarea,
            proyecto=tarea.proyecto,
        )
        if tarea.responsable_id:
            crear_notificacion(
                tarea.responsable.user,
                tarea.solicitud,
                f"Tarea reactivada #{tarea.id}",
                f"La tarea '{tarea.titulo}' fue reactivada.",
                tipo=Notificacion.TIPO_TAREA,
                tarea=tarea,
                proyecto=tarea.proyecto or getattr(tarea.solicitud, "proyecto", None),
            )
        messages.success(request, "Tarea reactivada.")
    if tarea.solicitud_id:
        return redirect("panel_solicitud_detalle", pk=tarea.solicitud_id)
    return redirect("panel_proyecto_detalle", proyecto_id=tarea.proyecto_id)


@login_required(login_url="panel_login")
def solicitud_orden_produccion(request, pk):
    solicitud = get_object_or_404(Solicitud.objects.select_related("producto__categoria"), pk=pk)
    if not usuario_puede_ver_solicitud(request.user, solicitud):
        raise PermissionDenied
    respuestas_produccion = respuestas_tecnicas_solicitud(solicitud)
    archivos_adjuntos = [respuesta for respuesta in respuestas_produccion if respuesta.archivo]
    return render(
        request,
        "tienda/panel/orden_produccion.html",
        {
            "solicitud": solicitud,
            "numero_orden": f"OP-{solicitud.id:06d}",
            "fecha_impresion": timezone.localtime(timezone.now()),
            "respuestas_produccion": respuestas_produccion,
            "archivos_adjuntos": archivos_adjuntos,
        },
    )


@panel_staff_required
def empleados_lista(request):
    q = request.GET.get("q", "").strip()
    area = request.GET.get("area", "").strip()
    activo = request.GET.get("activo", "").strip()
    empleados = EmpleadoPerfil.objects.select_related("user").all()
    if q:
        empleados = empleados.filter(
            Q(user__username__icontains=q)
            | Q(user__first_name__icontains=q)
            | Q(user__last_name__icontains=q)
            | Q(user__email__icontains=q)
            | Q(cargo__icontains=q)
        )
    if area:
        empleados = empleados.filter(area=area)
    if activo in ["1", "0"]:
        empleados = empleados.filter(activo=activo == "1")
    empleados = empleados.annotate(num_asignaciones=Count("asignaciones", filter=Q(asignaciones__activa=True), distinct=True))
    return render(
        request,
        "tienda/panel/empleados.html",
        {"empleados": empleados, "q": q, "area": area, "activo": activo, "areas": EmpleadoPerfil.AREAS},
    )


@panel_staff_required
def empleado_crear(request):
    form = EmpleadoPerfilForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        empleado = form.save()
        messages.success(request, f"Empleado {empleado} creado.")
        return redirect("panel_empleados")
    return render(request, "tienda/panel/empleado_form.html", {"form": form, "titulo": "Crear empleado"})


@panel_staff_required
def empleado_editar(request, empleado_id):
    empleado = get_object_or_404(EmpleadoPerfil.objects.select_related("user"), pk=empleado_id)
    form = EmpleadoPerfilForm(request.POST or None, instance=empleado)
    if request.method == "POST" and form.is_valid():
        empleado = form.save()
        messages.success(request, f"Empleado {empleado} actualizado.")
        return redirect("panel_empleados")
    return render(request, "tienda/panel/empleado_form.html", {"form": form, "empleado": empleado, "titulo": "Editar empleado"})


@panel_staff_required
@require_POST
def empleado_toggle(request, empleado_id):
    empleado = get_object_or_404(EmpleadoPerfil.objects.select_related("user"), pk=empleado_id)
    empleado.activo = not empleado.activo
    if not empleado.activo:
        empleado.puede_recibir_pedidos = False
    empleado.save(update_fields=["activo", "puede_recibir_pedidos", "actualizado"])
    messages.success(request, "Empleado actualizado.")
    return redirect("panel_empleados")


def produccion_login(request):
    redirect_value = request.POST.get(REDIRECT_FIELD_NAME) or request.GET.get(REDIRECT_FIELD_NAME, "")
    if request.user.is_authenticated:
        if request.user.is_staff:
            return redirect("panel_dashboard")
        return redirect("produccion_dashboard")
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        perfil = getattr(user, "empleado_perfil", None)
        if not user.is_staff and (perfil is None or not perfil.activo):
            form.add_error(None, "Tu usuario no tiene un perfil de producción activo.")
        else:
            login(request, user)
            if redirect_value and url_has_allowed_host_and_scheme(
                redirect_value,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(redirect_value)
            return redirect("panel_dashboard" if user.is_staff else "produccion_dashboard")
    return render(request, "tienda/produccion/login.html", {"form": form, REDIRECT_FIELD_NAME: redirect_value})


@login_required(login_url="produccion_login")
@require_POST
def produccion_logout(request):
    logout(request)
    return redirect("produccion_login")


def asignaciones_visibles_para_usuario(user):
    qs = SolicitudAsignacion.objects.filter(activa=True, empleado__activo=True).select_related(
        "solicitud__producto__categoria",
        "empleado__user",
    )
    if not user.is_staff:
        qs = qs.filter(empleado__user=user)
    return qs


def identificacion_produccion_usuario(user):
    perfil = getattr(user, "empleado_perfil", None)
    nombre = ""
    detalle = ""

    if perfil:
        nombre = str(perfil)
        detalle_partes = []
        if perfil.cargo:
            detalle_partes.append(perfil.cargo)
        area = perfil.get_area_display()
        if area and area not in detalle_partes:
            detalle_partes.append(area)
        detalle = " - ".join(detalle_partes)

    if not nombre:
        nombre = user.get_full_name().strip() or user.username

    if user.is_staff:
        detalle = "Vista global como administrador"

    return nombre, detalle


@produccion_required
def produccion_dashboard(request):
    q = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "").strip()
    tarea_estado = request.GET.get("tarea_estado", "").strip()
    proyecto_id = request.GET.get("proyecto", "").strip()
    fecha_limite = request.GET.get("fecha_limite", "").strip()
    asignaciones = asignaciones_visibles_para_usuario(request.user)
    if q:
        filtro = Q(solicitud__cliente_nombre__icontains=q) | Q(solicitud__producto__nombre__icontains=q)
        if q.isdigit():
            filtro = filtro | Q(solicitud__id=int(q))
        asignaciones = asignaciones.filter(filtro)
    if estado:
        asignaciones = asignaciones.filter(solicitud__estado_produccion=estado)

    estados_finalizados = [
        SolicitudTarea.ESTADO_TERMINADA,
        SolicitudTarea.ESTADO_APROBADA,
        SolicitudTarea.ESTADO_CANCELADA,
    ]
    tareas_autorizadas = tareas_visibles_para_usuario(request.user)
    if not request.user.is_staff:
        tareas_autorizadas = tareas_autorizadas.filter(responsable__user=request.user)

    tareas_base = tareas_autorizadas
    if not tarea_estado:
        tareas_base = tareas_base.exclude(estado__in=estados_finalizados)

    tareas = tareas_base
    if q:
        filtro_tareas = (
            Q(titulo__icontains=q)
            | Q(descripcion__icontains=q)
            | Q(solicitud__cliente_nombre__icontains=q)
            | Q(solicitud__producto__nombre__icontains=q)
            | Q(solicitud__proyecto__nombre__icontains=q)
            | Q(proyecto__nombre__icontains=q)
        )
        if q.isdigit():
            filtro_tareas = filtro_tareas | Q(solicitud__id=int(q)) | Q(id=int(q))
        tareas = tareas.filter(filtro_tareas)
    if tarea_estado:
        tareas = tareas.filter(estado=tarea_estado)
    if proyecto_id.isdigit():
        tareas = tareas.filter(Q(solicitud__proyecto_id=proyecto_id) | Q(proyecto_id=proyecto_id))
    if fecha_limite:
        tareas = tareas.filter(fecha_limite=fecha_limite)

    solicitudes_base = Solicitud.objects.filter(id__in=asignaciones.values("solicitud_id"))
    hoy = timezone.localdate()
    semana_inicio = hoy - timedelta(days=hoy.weekday())
    metricas = {
        "asignados": solicitudes_base.count(),
        "en_proceso": solicitudes_base.filter(estado_produccion=Solicitud.PROD_EN_PROCESO).count(),
        "con_novedad": solicitudes_base.filter(estado_produccion=Solicitud.PROD_CON_NOVEDAD).count(),
        "terminados_hoy": solicitudes_base.filter(estado_produccion=Solicitud.PROD_TERMINADO, actualizado__date=hoy).count(),
        "terminados_semana": solicitudes_base.filter(estado_produccion=Solicitud.PROD_TERMINADO, actualizado__date__gte=semana_inicio).count(),
    }
    metricas_tareas = {
        "asignadas": tareas_base.count(),
        "pendientes": tareas_base.filter(estado__in=[SolicitudTarea.ESTADO_PENDIENTE, SolicitudTarea.ESTADO_ASIGNADA]).count(),
        "en_proceso": tareas_base.filter(estado=SolicitudTarea.ESTADO_EN_PROCESO).count(),
        "vencidas": tareas_base.filter(fecha_limite__lt=hoy).exclude(estado__in=estados_finalizados).count(),
        "terminadas_hoy": tareas_autorizadas.filter(estado=SolicitudTarea.ESTADO_TERMINADA, fecha_finalizacion__date=hoy).count(),
    }
    proyectos = Proyecto.objects.filter(Q(solicitudes__tareas__in=tareas_base) | Q(tareas__in=tareas_base)).distinct().order_by("nombre")
    notificaciones = request.user.notificaciones.filter(leida=False).select_related("solicitud", "tarea", "proyecto")[:6]
    usuario_nombre, usuario_detalle = identificacion_produccion_usuario(request.user)
    return render(
        request,
        "tienda/produccion/dashboard.html",
        {
            "produccion_usuario_nombre": usuario_nombre,
            "produccion_usuario_detalle": usuario_detalle,
            "produccion_modo_staff": request.user.is_staff,
            "asignaciones": asignaciones.order_by("solicitud__estado_produccion", "-fecha_asignacion"),
            "tareas": tareas.annotate(
                es_vencida=Case(
                    When(fecha_limite__lt=hoy, then=Value(True)),
                    default=Value(False),
                    output_field=BooleanField(),
                ),
                orden_vencimiento=Case(
                    When(fecha_limite__lt=hoy, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                ),
                orden_prioridad=Case(
                    When(prioridad=SolicitudTarea.PRIORIDAD_URGENTE, then=Value(0)),
                    When(prioridad=SolicitudTarea.PRIORIDAD_ALTA, then=Value(1)),
                    When(prioridad=SolicitudTarea.PRIORIDAD_NORMAL, then=Value(2)),
                    default=Value(3),
                    output_field=IntegerField(),
                ),
            ).order_by("orden_vencimiento", "orden_prioridad", "fecha_limite", "orden")[:40],
            "metricas": metricas,
            "metricas_tareas": metricas_tareas,
            "notificaciones": notificaciones,
            "q": q,
            "estado": estado,
            "tarea_estado": tarea_estado,
            "proyecto_id": proyecto_id,
            "fecha_limite": fecha_limite,
            "estados": Solicitud.ESTADOS_PRODUCCION,
            "estados_tarea": SolicitudTarea.ESTADOS,
            "proyectos": proyectos,
        },
    )


@produccion_required
def produccion_pedido_detalle(request, solicitud_id):
    solicitud = get_object_or_404(Solicitud.objects.select_related("producto__categoria"), pk=solicitud_id)
    if not usuario_puede_ver_solicitud(request.user, solicitud):
        raise PermissionDenied

    novedad_form = SolicitudNovedadForm()
    estado_form = ProduccionEstadoForm(initial={"estado_produccion": solicitud.estado_produccion})
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "novedad":
            novedad_form = SolicitudNovedadForm(request.POST, request.FILES)
            if novedad_form.is_valid():
                novedad = novedad_form.save(commit=False)
                novedad.solicitud = solicitud
                novedad.usuario = request.user
                novedad.tipo = SolicitudNovedad.TIPO_EVIDENCIA if novedad.archivo_evidencia else SolicitudNovedad.TIPO_COMENTARIO
                novedad.save()
                notificar_asignados(solicitud, f"Novedad en solicitud #{solicitud.id}", novedad.comentario[:180], exclude_user=request.user)
                messages.success(request, "Novedad registrada.")
                return redirect("produccion_pedido_detalle", solicitud_id=solicitud.id)
        elif action == "estado":
            estado_form = ProduccionEstadoForm(request.POST)
            if estado_form.is_valid():
                cambiar_estado_produccion(
                    solicitud,
                    estado_form.cleaned_data["estado_produccion"],
                    request.user,
                    estado_form.cleaned_data.get("comentario", ""),
                )
                messages.success(request, "Estado actualizado.")
                return redirect("produccion_pedido_detalle", solicitud_id=solicitud.id)

    respuestas = respuestas_tecnicas_solicitud(solicitud)
    archivos_adjuntos = [respuesta for respuesta in respuestas if respuesta.archivo]
    asignaciones = solicitud.asignaciones.filter(activa=True).select_related("empleado__user")
    tareas = solicitud.tareas.filter(activa=True).select_related("responsable__user").order_by("orden", "fecha_limite", "id")
    if not request.user.is_staff:
        asignaciones = asignaciones.filter(empleado__user=request.user)
        tareas = tareas.filter(Q(responsable__user=request.user) | Q(solicitud__asignaciones__empleado__user=request.user, solicitud__asignaciones__activa=True)).distinct()
    novedades = solicitud.novedades.filter(visible_para_produccion=True).select_related("usuario").order_by("-fecha_creacion", "-id")
    return render(
        request,
        "tienda/produccion/pedido_detalle.html",
        {
            "solicitud": solicitud,
            "respuestas": respuestas,
            "archivos_adjuntos": archivos_adjuntos,
            "asignaciones": asignaciones,
            "tareas": tareas,
            "novedades": novedades,
            "novedad_form": novedad_form,
            "estado_form": estado_form,
        },
    )


@produccion_required
def produccion_tarea_detalle(request, tarea_id):
    tarea = get_object_or_404(
        SolicitudTarea.objects.select_related(
            "solicitud__producto__categoria",
            "solicitud__proyecto",
            "proyecto",
            "responsable__user",
        ),
        pk=tarea_id,
    )
    if not usuario_puede_ver_tarea(request.user, tarea):
        raise PermissionDenied

    estado_form = ProduccionTareaEstadoForm(initial={"estado": tarea.estado})
    novedad_form = SolicitudNovedadForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "estado":
            estado_form = ProduccionTareaEstadoForm(request.POST)
            if estado_form.is_valid():
                cambiar_estado_tarea(
                    tarea,
                    estado_form.cleaned_data["estado"],
                    request.user,
                    estado_form.cleaned_data.get("comentario", ""),
                )
                messages.success(request, "Estado de tarea actualizado.")
                return redirect("produccion_tarea_detalle", tarea_id=tarea.id)
        elif action == "novedad":
            novedad_form = SolicitudNovedadForm(request.POST, request.FILES)
            if novedad_form.is_valid():
                novedad = novedad_form.save(commit=False)
                novedad.solicitud = tarea.solicitud
                novedad.proyecto = tarea.proyecto or getattr(tarea.solicitud, "proyecto", None)
                novedad.tarea = tarea
                novedad.usuario = request.user
                novedad.tipo = SolicitudNovedad.TIPO_TAREA_EVIDENCIA if novedad.archivo_evidencia else SolicitudNovedad.TIPO_TAREA_COMENTARIO
                novedad.save()
                if novedad.archivo_evidencia:
                    tarea.evidencia_archivo = novedad.archivo_evidencia
                    tarea.save(update_fields=["evidencia_archivo", "fecha_actualizacion"])
                notificar_tarea(
                    tarea,
                    f"Novedad en tarea #{tarea.id}",
                    novedad.comentario[:180],
                    tipo=Notificacion.TIPO_TAREA,
                    exclude_user=request.user,
                )
                messages.success(request, "Novedad de tarea registrada.")
                return redirect("produccion_tarea_detalle", tarea_id=tarea.id)

    respuestas = respuestas_tecnicas_solicitud(tarea.solicitud) if tarea.solicitud_id else []
    archivos_adjuntos = [respuesta for respuesta in respuestas if respuesta.archivo]
    novedades = tarea.novedades.filter(visible_para_produccion=True).select_related("usuario").order_by("-fecha_creacion", "-id")
    return render(
        request,
        "tienda/produccion/tarea_detalle.html",
        {
            "tarea": tarea,
            "solicitud": tarea.solicitud,
            "respuestas": respuestas,
            "archivos_adjuntos": archivos_adjuntos,
            "novedades": novedades,
            "estado_form": estado_form,
            "novedad_form": novedad_form,
        },
    )


@produccion_required
def produccion_notificaciones_check(request):
    notificaciones = request.user.notificaciones.filter(leida=False).select_related("solicitud", "tarea", "proyecto")[:5]
    return JsonResponse(
        {
            "unread_count": request.user.notificaciones.filter(leida=False).count(),
            "notifications": [
                {
                    "id": n.id,
                    "titulo": n.titulo,
                    "mensaje": n.mensaje,
                    "url": n.url_destino,
                    "fecha": timezone.localtime(n.fecha_creacion).strftime("%d/%m/%Y %H:%M"),
                }
                for n in notificaciones
            ],
        }
    )


@produccion_required
@require_POST
def produccion_notificaciones_marcar_leidas(request):
    request.user.notificaciones.filter(leida=False).update(leida=True)
    return redirect("produccion_dashboard")


@panel_staff_required
def productos_lista(request):
    q = request.GET.get("q", "").strip()
    categoria_id = request.GET.get("categoria", "").strip()
    activo = request.GET.get("activo", "").strip()
    destacado = request.GET.get("destacado", "").strip()
    productos = Producto.objects.select_related("categoria", "unspsc").annotate(
        num_campos=Count("campos", distinct=True),
        num_solicitudes=Count("solicitudes", distinct=True),
    )

    if q:
        productos = productos.filter(Q(nombre__icontains=q) | Q(slug__icontains=q))
    if categoria_id.isdigit():
        productos = productos.filter(categoria_id=categoria_id)
    if activo in ["1", "0"]:
        productos = productos.filter(activo=activo == "1")
    if destacado in ["1", "0"]:
        productos = productos.filter(destacado=destacado == "1")
    productos = productos.order_by("orden", "nombre")
    categorias = Categoria.objects.order_by("orden", "nombre")
    return render(
        request,
        "tienda/panel/productos.html",
        {
            "productos": productos,
            "categorias": categorias,
            "q": q,
            "categoria_id": categoria_id,
            "activo": activo,
            "destacado": destacado,
        },
    )


@panel_staff_required
def unspsc_catalogo(request):
    form = UNSPSCImportJobForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        job = form.save(commit=False)
        job.created_by = request.user
        job.status = UNSPSCImportJob.STATUS_PENDING
        job.save()
        messages.success(request, "Archivo recibido. La validación se ejecutará mediante el comando programado.")
        return redirect("panel_unspsc_catalogo")
    latest = UNSPSCCode.objects.order_by("-imported_at").first()
    current_version = latest.catalog_version if latest else "-"
    codes = UNSPSCCode.objects.filter(catalog_version=current_version) if latest else UNSPSCCode.objects.none()
    levels = list(codes.values("level").annotate(total=Count("id")).order_by("level"))
    jobs = UNSPSCImportJob.objects.select_related("created_by").all()[:10]
    return render(request, "tienda/panel/unspsc_catalogo.html", {"form": form, "current_version": current_version, "last_update": latest.imported_at if latest else None, "total_codes": codes.count(), "levels": levels, "jobs": jobs})


@panel_staff_required
@require_POST
def unspsc_importacion_confirmar(request, job_id):
    job = get_object_or_404(UNSPSCImportJob, pk=job_id)
    if job.status != UNSPSCImportJob.STATUS_READY:
        messages.error(request, "El trabajo no tiene una vista previa lista para confirmar.")
    else:
        job.status = UNSPSCImportJob.STATUS_APPLY_REQUESTED
        job.save(update_fields=["status"])
        messages.success(request, "Aplicación solicitada. El comando programado la procesará fuera de la petición web.")
    return redirect("panel_unspsc_catalogo")


@panel_staff_required
def campos_maestros_lista(request):
    q = request.GET.get("q", "").strip()
    tipo = request.GET.get("tipo", "").strip()
    activo = request.GET.get("activo", "").strip()
    campos = CampoMaestro.objects.annotate(
        usos=Count("producto_campos", distinct=True),
        num_opciones=Count("opciones_maestras", distinct=True),
    )
    if q:
        campos = campos.filter(Q(nombre__icontains=q) | Q(slug__icontains=q) | Q(etiqueta_base__icontains=q))
    if tipo:
        campos = campos.filter(tipo=tipo)
    if activo in ["1", "0"]:
        campos = campos.filter(activo=activo == "1")
    campos = campos.order_by("orden_base", "nombre")
    return render(
        request,
        "tienda/panel/campos_maestros.html",
        {"campos": campos, "q": q, "tipo": tipo, "activo": activo, "tipos": ProductoCampo.TIPO_CHOICES},
    )


@panel_staff_required
def campo_maestro_crear(request):
    form = CampoMaestroForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Campo maestro creado.")
        return redirect("panel_campos_maestros")
    return render(request, "tienda/panel/campo_maestro_form.html", {"form": form, "titulo": "Crear campo maestro"})


@panel_staff_required
def campo_maestro_editar(request, maestro_id):
    campo = get_object_or_404(CampoMaestro, pk=maestro_id)
    form = CampoMaestroForm(request.POST or None, instance=campo)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Campo maestro actualizado.")
        return redirect("panel_campos_maestros")
    return render(request, "tienda/panel/campo_maestro_form.html", {"form": form, "campo": campo, "titulo": "Editar campo maestro"})


@panel_staff_required
def campo_maestro_opciones(request, maestro_id):
    campo = get_object_or_404(CampoMaestro, pk=maestro_id)
    opciones = campo.opciones_maestras.order_by("orden", "id")
    return render(request, "tienda/panel/campo_maestro_opciones.html", {"campo": campo, "opciones": opciones})


@panel_staff_required
def campo_maestro_opcion_crear(request, maestro_id):
    campo = get_object_or_404(CampoMaestro, pk=maestro_id)
    form = CampoMaestroOpcionForm(request.POST or None, campo_maestro=campo)
    if request.method == "POST" and form.is_valid():
        opcion = form.save(commit=False)
        opcion.campo_maestro = campo
        opcion.save()
        messages.success(request, "Opción maestra creada.")
        return redirect("panel_campo_maestro_opciones", maestro_id=campo.id)
    return render(request, "tienda/panel/campo_maestro_opcion_form.html", {"form": form, "campo": campo, "titulo": "Crear opción maestra"})


@panel_staff_required
def campo_maestro_opcion_editar(request, opcion_id):
    opcion = get_object_or_404(CampoMaestroOpcion.objects.select_related("campo_maestro"), pk=opcion_id)
    form = CampoMaestroOpcionForm(request.POST or None, instance=opcion)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Opción maestra actualizada.")
        return redirect("panel_campo_maestro_opciones", maestro_id=opcion.campo_maestro.id)
    return render(request, "tienda/panel/campo_maestro_opcion_form.html", {"form": form, "campo": opcion.campo_maestro, "opcion": opcion, "titulo": "Editar opción maestra"})


@panel_staff_required
def producto_crear(request):
    max_orden = Producto.objects.aggregate(max_orden=Max("orden"))["max_orden"]
    siguiente_orden = (max_orden if max_orden is not None else 0) + 1
    datos = request.POST or None
    if datos is not None and datos.get("orden", "") == "":
        datos = datos.copy()
        datos["orden"] = siguiente_orden
    form = ProductoForm(datos, request.FILES or None, initial={"orden": siguiente_orden})
    if request.method == "POST" and form.is_valid():
        producto = form.save()
        messages.success(request, "Producto creado. Ahora configura sus campos y opciones.")
        return redirect("panel_producto_campos", producto_id=producto.id)
    return render(request, "tienda/panel/producto_form.html", {"form": form, "titulo": "Crear producto"})


@panel_staff_required
def producto_editar(request, producto_id):
    producto = get_object_or_404(Producto, pk=producto_id)
    form = ProductoForm(request.POST or None, request.FILES or None, instance=producto)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Producto actualizado.")
        return redirect("panel_productos")
    return render(request, "tienda/panel/producto_form.html", {"form": form, "producto": producto, "titulo": "Editar producto"})


@panel_staff_required
@require_GET
def producto_unspsc_buscar(request):
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})
    return JsonResponse({"results": [unspsc_result(item) for item in recommend_unspsc(query)]})


@panel_staff_required
@require_POST
def producto_unspsc_quitar(request, producto_id):
    producto = get_object_or_404(Producto, pk=producto_id)
    producto.unspsc = None
    producto.save(update_fields=["unspsc", "actualizado"])
    messages.success(request, "Clasificación UNSPSC eliminada explícitamente.")
    return redirect("panel_producto_editar", producto_id=producto.id)


@panel_staff_required
def producto_campos(request, producto_id):
    producto = get_object_or_404(Producto, pk=producto_id)
    q = request.GET.get("q", "").strip()
    for campo in producto.campos.select_related("campo_maestro").prefetch_related("opciones", "campo_maestro__opciones_maestras"):
        asegurar_opciones_para_campo(campo)

    campos = producto.campos.filter(activo=True).select_related("campo_maestro").prefetch_related("opciones").order_by("orden", "id")
    if q:
        campos = campos.filter(
            Q(etiqueta__icontains=q)
            | Q(nombre_interno__icontains=q)
            | Q(tipo__icontains=q)
            | Q(campo_maestro__nombre__icontains=q)
            | Q(campo_maestro__slug__icontains=q)
        )
    maestros_asignados = producto.campos.filter(activo=True, campo_maestro__isnull=False).values_list("campo_maestro_id", flat=True)
    maestros_disponibles = (
        CampoMaestro.objects.filter(activo=True)
        .exclude(pk__in=maestros_asignados)
        .prefetch_related(
            Prefetch(
                "opciones_maestras",
                queryset=CampoMaestroOpcion.objects.filter(activa=True).order_by("orden", "id"),
                to_attr="opciones_activas",
            )
        )
        .order_by("orden_base", "nombre")
    )
    if q:
        maestros_disponibles = maestros_disponibles.filter(
            Q(nombre__icontains=q) | Q(slug__icontains=q) | Q(etiqueta_base__icontains=q) | Q(tipo__icontains=q)
        )
    return render(
        request,
        "tienda/panel/producto_campos.html",
        {"producto": producto, "campos": campos, "maestros_disponibles": maestros_disponibles, "q": q},
    )


@panel_staff_required
def campo_crear(request, producto_id):
    producto = get_object_or_404(Producto, pk=producto_id)
    form = ProductoCampoForm(request.POST or None, producto=producto)
    if request.method == "POST" and form.is_valid():
        campo = form.save(commit=False)
        campo.producto = producto
        campo.save()
        messages.success(request, "Campo creado.")
        return redirect("panel_producto_campos", producto_id=producto.id)
    return render(request, "tienda/panel/campo_form.html", {"form": form, "producto": producto, "titulo": "Crear campo"})


@panel_staff_required
@require_POST
def campo_asignar_maestro(request, producto_id, maestro_id):
    producto = get_object_or_404(Producto, pk=producto_id)
    maestro = get_object_or_404(CampoMaestro, pk=maestro_id, activo=True)
    campo_existente = ProductoCampo.objects.filter(producto=producto, campo_maestro=maestro).first()
    if campo_existente:
        if campo_existente.activo:
            messages.warning(request, "Ese campo maestro ya esta asignado a este producto.")
        else:
            campo_existente.activo = True
            try:
                campo_existente.save()
                creadas = asegurar_opciones_para_campo(campo_existente, solo_si_sin_opciones=False)
                detalle = f" Se copiaron {creadas} opciones faltantes." if creadas else ""
                messages.success(request, f"Campo reactivado para este producto.{detalle}")
            except ValidationError as exc:
                messages.error(request, validation_message(exc))
        return redirect("panel_producto_campos", producto_id=producto.id)

    campo = ProductoCampo.desde_maestro(producto, maestro)
    try:
        campo.save()
        creadas = asegurar_opciones_para_campo(campo, solo_si_sin_opciones=False)
        detalle = f" Se copiaron {creadas} opciones." if creadas else ""
        messages.success(request, f"Campo maestro asignado al producto.{detalle}")
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
    return redirect("panel_producto_campos", producto_id=producto.id)


@panel_staff_required
@require_POST
def campo_desactivar(request, campo_id):
    campo = get_object_or_404(ProductoCampo.objects.select_related("producto"), pk=campo_id)
    campo.activo = False
    campo.save()
    messages.success(request, "Campo quitado del formulario público de este producto.")
    return redirect("panel_producto_campos", producto_id=campo.producto.id)


@panel_staff_required
@require_POST
def campo_reactivar(request, campo_id):
    campo = get_object_or_404(ProductoCampo.objects.select_related("producto", "campo_maestro"), pk=campo_id)
    campo.activo = True
    try:
        campo.save()
        creadas = asegurar_opciones_para_campo(campo, solo_si_sin_opciones=False)
        detalle = f" Se copiaron {creadas} opciones faltantes." if creadas else ""
        messages.success(request, f"Campo reactivado.{detalle}")
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
    return redirect("panel_producto_campos", producto_id=campo.producto.id)


@panel_staff_required
@require_POST
def campo_copiar_opciones_maestro(request, campo_id):
    campo = get_object_or_404(ProductoCampo.objects.select_related("producto", "campo_maestro"), pk=campo_id)
    creadas = campo.copiar_opciones_maestras()
    if creadas:
        messages.success(request, f"Se copiaron {creadas} opciones maestras faltantes.")
    else:
        messages.info(request, "No había opciones maestras faltantes para copiar.")
    return redirect("panel_producto_campos", producto_id=campo.producto.id)


@panel_staff_required
def campo_editar(request, campo_id):
    campo = get_object_or_404(ProductoCampo.objects.select_related("producto"), pk=campo_id)
    form = ProductoCampoForm(request.POST or None, instance=campo)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Campo actualizado.")
        return redirect("panel_producto_campos", producto_id=campo.producto.id)
    return render(request, "tienda/panel/campo_form.html", {"form": form, "producto": campo.producto, "campo": campo, "titulo": "Editar campo"})


@panel_staff_required
def campo_opciones(request, campo_id):
    campo = get_object_or_404(ProductoCampo.objects.select_related("producto"), pk=campo_id)
    opciones = campo.opciones.order_by("orden", "id")
    return render(request, "tienda/panel/campo_opciones.html", {"campo": campo, "opciones": opciones})


@panel_staff_required
def opcion_crear(request, campo_id):
    campo = get_object_or_404(ProductoCampo.objects.select_related("producto"), pk=campo_id)
    form = CampoOpcionForm(request.POST or None, campo=campo)
    if request.method == "POST" and form.is_valid():
        opcion = form.save(commit=False)
        opcion.campo = campo
        opcion.save()
        messages.success(request, "Opción creada.")
        return redirect("panel_campo_opciones", campo_id=campo.id)
    return render(request, "tienda/panel/opcion_form.html", {"form": form, "campo": campo, "titulo": "Crear opción"})


@panel_staff_required
def opcion_editar(request, opcion_id):
    opcion = get_object_or_404(CampoOpcion.objects.select_related("campo__producto"), pk=opcion_id)
    form = CampoOpcionForm(request.POST or None, instance=opcion)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Opción actualizada.")
        return redirect("panel_campo_opciones", campo_id=opcion.campo.id)
    return render(request, "tienda/panel/opcion_form.html", {"form": form, "campo": opcion.campo, "opcion": opcion, "titulo": "Editar opción"})


@panel_staff_required
def producto_imagenes(request, producto_id):
    producto = get_object_or_404(Producto, pk=producto_id)
    form = ProductoImagenForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        img = form.save(commit=False)
        img.producto = producto
        img.save()
        messages.success(request, "Imagen agregada.")
        return redirect("panel_producto_imagenes", producto_id=producto.id)
    imagenes = producto.imagenes.order_by("orden", "id")
    return render(request, "tienda/panel/producto_imagenes.html", {"producto": producto, "form": form, "imagenes": imagenes})


@panel_staff_required
@require_POST
def producto_toggle(request, producto_id):
    producto = get_object_or_404(Producto, pk=producto_id)
    if not producto.activo and (not producto.categoria_id or not producto.tipo_calculo):
        messages.error(request, "No se puede activar un producto pendiente de categoría o tipo de cálculo.")
        return redirect("panel_productos")
    producto.activo = not producto.activo
    producto.save(update_fields=["activo"])
    return redirect("panel_productos")


@panel_staff_required
@require_POST
def imagen_eliminar(request, imagen_id):
    imagen = get_object_or_404(ProductoImagen, pk=imagen_id)
    producto_id = imagen.producto_id
    imagen.delete()
    messages.success(request, "Imagen eliminada.")
    return redirect("panel_producto_imagenes", producto_id=producto_id)


@alegra_staff_required
def alegra_integraciones(request):
    system = ExternalSystem.objects.filter(code="alegra").first()
    staging = AlegraItemStaging.objects.all()
    last_audit = SyncAuditLog.objects.filter(resource="items").first()
    connection_status = last_audit.get_result_display() if last_audit else "Sin consulta"
    context = {
        "system": system,
        "last_audit": last_audit,
        "connection_status": connection_status,
        "last_sync": staging.order_by("-fetched_at").values_list("fetched_at", flat=True).first(),
        "metrics": {
            "consultados": staging.count(),
            "pendientes": staging.filter(review_status=AlegraItemStaging.REVIEW_PENDING).count(),
            "coincidencias": staging.filter(review_status=AlegraItemStaging.REVIEW_MATCH).count(),
            "conflictos": staging.filter(review_status=AlegraItemStaging.REVIEW_CONFLICT).count(),
            "importados": staging.filter(review_status=AlegraItemStaging.REVIEW_IMPORTED).count(),
            "errores": staging.filter(review_status=AlegraItemStaging.REVIEW_ERROR).count(),
        },
        "alegra_status": alegra_operational_status(),
    }
    return render(request, "tienda/panel/alegra_integraciones.html", context)


@alegra_staff_required
@require_GET
def alegra_connection_status(request):
    force = request.GET.get("force") == "1"
    state = check_connection(force=force) if force else connection_state()
    state["integrations_url"] = reverse("alegra_integraciones")
    state["manual_allowed"] = True
    return JsonResponse(state)


@alegra_change_required
@require_POST
def alegra_sync_items(request):
    raw_limit = request.POST.get("limit", "100")
    try:
        limit = min(max(int(raw_limit), 1), 300)
    except ValueError:
        limit = 100
    result = AlegraItemImporter().sync(limit=limit, actor=request.user)
    if result["errors"]:
        messages.error(request, "No se pudo consultar Alegra: " + result["errors"][0])
    else:
        messages.success(request, f"Consulta completada: {result['total']} ítems procesados.")
    return redirect("alegra_catalogo")


@alegra_staff_required
def alegra_catalogo(request):
    qs = AlegraItemStaging.objects.select_related("system", "matched_product", "imported_product")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    classification = request.GET.get("classification", "").strip()
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(reference__icontains=query) | Q(external_id__icontains=query))
    if status in {choice[0] for choice in AlegraItemStaging.REVIEW_STATUSES}:
        qs = qs.filter(review_status=status)
    if classification in {choice[0] for choice in AlegraItemStaging.CLASSIFICATIONS}:
        qs = qs.filter(classification=classification)
    paginator = Paginator(qs.order_by("-fetched_at", "name"), 25)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "tienda/panel/alegra_catalogo.html",
        {
            "page_obj": page,
            "query": query,
            "status": status,
            "classification": classification,
            "statuses": AlegraItemStaging.REVIEW_STATUSES,
            "classifications": AlegraItemStaging.CLASSIFICATIONS,
            "productos": Producto.objects.order_by("nombre"),
            "categorias": Categoria.objects.filter(activa=True).order_by("orden", "nombre"),
            "calculo_choices": Producto.CALCULO_CHOICES,
        },
    )


def _alegra_selected_ids(request):
    values = request.POST.getlist("selected_ids")
    selected = []
    for value in values[:300]:
        try:
            item_id = int(value)
        except (TypeError, ValueError):
            continue
        if item_id > 0 and item_id not in selected:
            selected.append(item_id)
    return selected


def _alegra_bulk_preview_data(selected_ids, operation):
    records = list(AlegraItemStaging.objects.select_related("matched_product").filter(pk__in=selected_ids).order_by("pk"))
    reasons = {}
    eligible = []
    for record in records:
        if operation == "ignore":
            allowed = record.classification not in {AlegraItemStaging.CLASS_LINKED, AlegraItemStaging.CLASS_IGNORED}
            reason = "Ya está vinculado o ignorado." if not allowed else ""
        elif operation == "classify":
            allowed = bool(record.imported_product_id)
            reason = "No existe producto local importado para clasificar." if not allowed else ""
        else:
            allowed = record.classification == AlegraItemStaging.CLASS_NEW and not record.matched_product_id and not record.imported_product_id
            reason = "Debe estar clasificado como nuevo, sin coincidencia ni importación previa." if not allowed else ""
        if allowed:
            eligible.append(record)
        else:
            reasons[reason] = reasons.get(reason, 0) + 1
    return {"records": records, "eligible": eligible, "blocked": len(records) - len(eligible), "reasons": reasons}


@alegra_change_required
@require_POST
def alegra_bulk_classify(request):
    selected_ids = _alegra_selected_ids(request)
    if not selected_ids:
        messages.warning(request, "Selecciona al menos un registro.")
        return redirect("alegra_catalogo")
    counts = AlegraItemReconciler().classify(staging_ids=selected_ids, actor=request.user)
    messages.success(request, "Clasificación completada: " + ", ".join(f"{count} {dict(AlegraItemStaging.CLASSIFICATIONS).get(key, key)}" for key, count in counts.items() if count))
    return redirect("alegra_catalogo")


@alegra_change_required
@require_POST
def alegra_bulk_preview(request):
    operation = request.POST.get("operation", "").strip()
    if operation not in {"ignore", "import", "classify"}:
        messages.error(request, "Operación masiva no válida.")
        return redirect("alegra_catalogo")
    selected_ids = _alegra_selected_ids(request)
    if not selected_ids:
        messages.warning(request, "Selecciona al menos un registro.")
        return redirect("alegra_catalogo")
    context = _alegra_bulk_preview_data(selected_ids, operation)
    context.update({"operation": operation, "selected_ids": selected_ids, "categorias": Categoria.objects.filter(activa=True).order_by("orden", "nombre"), "calculo_choices": Producto.CALCULO_CHOICES})
    return render(request, "tienda/panel/alegra_bulk_preview.html", context)


@alegra_change_required
@require_POST
def alegra_bulk_confirm(request):
    operation = request.POST.get("operation", "").strip()
    selected_ids = _alegra_selected_ids(request)
    if operation not in {"ignore", "import", "classify"} or not selected_ids:
        messages.error(request, "Confirmación masiva inválida.")
        return redirect("alegra_catalogo")
    data = _alegra_bulk_preview_data(selected_ids, operation)
    importer = AlegraItemImporter()
    success = 0
    errors = []
    for record in data["eligible"]:
        try:
            if operation == "ignore":
                importer.ignore_item(record.pk, actor=request.user)
            elif operation == "classify":
                importer.classify_imported_item(record.pk, category_id=request.POST.get("categoria_id") or None, calculation_type=request.POST.get("tipo_calculo") or None, actor=request.user)
            else:
                importer.import_item(record.pk, category_id=request.POST.get("categoria_id") or None, calculation_type=request.POST.get("tipo_calculo") or None, actor=request.user)
            success += 1
        except Exception as exc:
            errors.append(f"{record.name}: {str(exc)[:160]}")
    if data["records"]:
        SyncAuditLog.objects.create(
            system=data["records"][0].system,
            operation=f"bulk_{operation}",
            resource="items",
            actor=request.user,
            result=SyncAuditLog.RESULT_SUCCESS if not errors and not data["blocked"] else SyncAuditLog.RESULT_PARTIAL,
            detail=f"Procesados: {success}; bloqueados: {data['blocked']}; errores: {len(errors)}.",
            metadata={"selected": len(data["records"]), "success": success, "blocked": data["blocked"], "errors": len(errors)},
        )
    if success:
        messages.success(request, f"Operación completada para {success} registro(s).")
    if data["blocked"] or errors:
        messages.warning(request, f"Operación parcial: {data['blocked']} bloqueado(s), {len(errors)} con error.")
        if errors:
            messages.error(request, " | ".join(errors[:5]))
    return redirect("alegra_catalogo")


@alegra_change_required
@require_POST
def alegra_item_link(request, staging_id):
    try:
        product_id = int(request.POST.get("producto_id", ""))
        AlegraItemImporter().link_item(staging_id, product_id, actor=request.user)
        messages.success(request, "Ítem de Alegra vinculado con el producto local.")
    except (ValueError, Producto.DoesNotExist, AlegraItemStaging.DoesNotExist) as exc:
        messages.error(request, f"No se pudo vincular: {exc}")
    return redirect("alegra_catalogo")


@alegra_change_required
@require_POST
def alegra_item_ignore(request, staging_id):
    try:
        AlegraItemImporter().ignore_item(staging_id, actor=request.user)
        messages.success(request, "Ítem marcado como ignorado.")
    except AlegraItemStaging.DoesNotExist:
        messages.error(request, "El ítem no existe.")
    return redirect("alegra_catalogo")


@alegra_change_required
@require_POST
def alegra_item_import(request, staging_id):
    try:
        category_id = request.POST.get("categoria_id") or None
        product = AlegraItemImporter().import_item(
            staging_id,
            category_id=category_id,
            calculation_type=request.POST.get("tipo_calculo", ""),
            actor=request.user,
        )
        messages.success(request, f"Producto importado como inactivo: {product.nombre}.")
    except (ValueError, AlegraItemStaging.DoesNotExist) as exc:
        messages.error(request, f"No se pudo importar: {exc}")
    return redirect("alegra_catalogo")


@alegra_change_required
@require_POST
def alegra_item_classify(request, staging_id):
    try:
        product = AlegraItemImporter().classify_imported_item(
            staging_id,
            category_id=request.POST.get("categoria_id") or None,
            calculation_type=request.POST.get("tipo_calculo") or None,
            actor=request.user,
        )
        messages.success(request, f"Clasificación local actualizada para {product.nombre}.")
    except (ValueError, AlegraItemStaging.DoesNotExist) as exc:
        messages.error(request, f"No se pudo clasificar: {exc}")
    return redirect("alegra_catalogo")


@alegra_staff_required
def alegra_historial(request):
    logs = SyncAuditLog.objects.select_related("actor", "system").order_by("-created_at", "-id")
    paginator = Paginator(logs, 30)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "tienda/panel/alegra_historial.html", {"page_obj": page})


def _alegra_contact_selected_ids(request):
    selected = []
    for value in request.POST.getlist("selected_ids")[:300]:
        try:
            item_id = int(value)
        except (TypeError, ValueError):
            continue
        if item_id > 0 and item_id not in selected:
            selected.append(item_id)
    return selected


@alegra_contact_change_required
@require_POST
def alegra_sync_contacts(request):
    try:
        limit = min(max(int(request.POST.get("limit", "100")), 1), 300)
    except ValueError:
        limit = 100
    result = AlegraContactImporter().sync(limit=limit, actor=request.user)
    if result["errors"]:
        messages.error(request, f"No se pudo completar la consulta de Alegra: {result['errors'][0]} Contactos procesados: {result['total']}; nuevos en staging: {result['created']}; actualizados: {result['updated']}. Los registros anteriores se conservaron.")
    else:
        messages.success(request, f"Consulta completada: {result['total']} contactos consultados; {result['created']} nuevos en staging; {result['updated']} actualizados; errores: 0.")
    return redirect("alegra_contactos_catalogo")


@alegra_contact_staff_required
def alegra_contactos_catalogo(request):
    qs = AlegraContactStaging.objects.select_related("matched_client", "imported_client")
    query = request.GET.get("q", "").strip()
    classification = request.GET.get("classification", "").strip()
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(identification__icontains=query) | Q(email__icontains=query) | Q(external_id__icontains=query))
    if classification in {value for value, _label in AlegraContactStaging.CLASSIFICATIONS}:
        qs = qs.filter(classification=classification)
    page = Paginator(qs.order_by("-fetched_at", "name"), 25).get_page(request.GET.get("page"))
    alegra_system = ExternalSystem.objects.filter(code="alegra").first()
    mapped_clients = ExternalObjectMap.objects.filter(
        system=alegra_system,
        resource_type="contacts",
        status=ExternalObjectMap.STATUS_ACTIVE,
        content_type__model="cliente",
    ).count() if alegra_system else 0
    mapped_client_ids = set(ExternalObjectMap.objects.filter(
        system=alegra_system,
        resource_type="contacts",
        status=ExternalObjectMap.STATUS_ACTIVE,
        content_type__model="cliente",
        object_id__isnull=False,
    ).values_list("object_id", flat=True)) if alegra_system else set()
    last_contact_sync = SyncAuditLog.objects.filter(resource="contacts").order_by("-created_at", "-id").first()
    counts = {
        "linked": AlegraContactStaging.objects.filter(classification=AlegraContactStaging.CLASS_LINKED).count(),
        "pending": AlegraContactStaging.objects.filter(classification__in=[AlegraContactStaging.CLASS_NEW, AlegraContactStaging.CLASS_PROBABLE, AlegraContactStaging.CLASS_INCOMPLETE]).count(),
        "conflict": AlegraContactStaging.objects.filter(classification=AlegraContactStaging.CLASS_CONFLICT).count(),
        "errors": AlegraContactStaging.objects.filter(error_detail__gt="").count(),
        "external_unlinked": AlegraContactStaging.objects.exclude(classification__in=[AlegraContactStaging.CLASS_LINKED, AlegraContactStaging.CLASS_IGNORED]).count(),
        "mapped_clients": mapped_clients,
        "local_pending": Cliente.objects.exclude(pk__in=mapped_client_ids).count(),
        "last_contact_sync": last_contact_sync,
        "recent_sync_errors": SyncAuditLog.objects.filter(resource="contacts", result=SyncAuditLog.RESULT_ERROR).count(),
    }
    return render(request, "tienda/panel/alegra_contactos.html", {
        "page_obj": page,
        "query": query,
        "classification": classification,
        "classifications": AlegraContactStaging.CLASSIFICATIONS,
        "clientes": Cliente.objects.order_by("nombre"),
        "bidirectional_safe_mode": True,
        "bidirectional_counts": counts,
    })


@alegra_contact_change_required
@require_POST
def alegra_contactos_clasificar(request):
    selected = _alegra_contact_selected_ids(request)
    if not selected:
        messages.warning(request, "Selecciona al menos un contacto.")
    else:
        counts = AlegraContactReconciler().classify(staging_ids=selected, actor=request.user)
        messages.success(request, "Clasificación completada: " + ", ".join(f"{count} {dict(AlegraContactStaging.CLASSIFICATIONS).get(key, key)}" for key, count in counts.items() if count))
    return redirect("alegra_contactos_catalogo")


def _contact_bulk_data(selected_ids, operation):
    records = list(AlegraContactStaging.objects.select_related("matched_client").filter(pk__in=selected_ids).order_by("pk"))
    eligible, reasons = [], {}
    for record in records:
        allowed = record.classification not in {AlegraContactStaging.CLASS_LINKED, AlegraContactStaging.CLASS_IGNORED} if operation == "ignore" else record.classification == AlegraContactStaging.CLASS_NEW and not record.matched_client_id and not record.imported_client_id
        if allowed:
            eligible.append(record)
        else:
            reason = "Ya está vinculado o ignorado." if operation == "ignore" else "Debe estar clasificado como nuevo, sin coincidencia ni importación previa."
            reasons[reason] = reasons.get(reason, 0) + 1
    return {"records": records, "eligible": eligible, "blocked": len(records) - len(eligible), "reasons": reasons}


@alegra_contact_change_required
@require_POST
def alegra_contactos_preview(request):
    operation = request.POST.get("operation", "")
    selected = _alegra_contact_selected_ids(request)
    if operation not in {"import", "ignore"} or not selected:
        messages.error(request, "Operación masiva inválida.")
        return redirect("alegra_contactos_catalogo")
    context = _contact_bulk_data(selected, operation)
    context.update({"operation": operation, "selected_ids": selected})
    return render(request, "tienda/panel/alegra_contactos_preview.html", context)


@alegra_contact_change_required
@require_POST
def alegra_contactos_confirmar(request):
    operation = request.POST.get("operation", "")
    selected = _alegra_contact_selected_ids(request)
    data = _contact_bulk_data(selected, operation) if operation in {"import", "ignore"} else {"records": [], "eligible": [], "blocked": 0}
    if not data["records"]:
        messages.error(request, "Confirmación masiva inválida.")
        return redirect("alegra_contactos_catalogo")
    importer = AlegraContactImporter()
    success, errors = 0, []
    for record in data["eligible"]:
        try:
            importer.import_contact(record.pk, actor=request.user) if operation == "import" else importer.ignore_contact(record.pk, actor=request.user)
            success += 1
        except Exception as exc:
            errors.append(f"{record.name}: {str(exc)[:160]}")
    if success:
        messages.success(request, f"Operación completada para {success} contacto(s).")
    if data["blocked"] or errors:
        messages.warning(request, f"Operación parcial: {data['blocked']} bloqueado(s), {len(errors)} con error.")
    return redirect("alegra_contactos_catalogo")


@alegra_contact_change_required
@require_POST
def alegra_contacto_vincular(request, staging_id):
    try:
        AlegraContactImporter().link_contact(staging_id, int(request.POST.get("cliente_id", "")), actor=request.user)
        messages.success(request, "Contacto de Alegra vinculado con el cliente local.")
    except Exception as exc:
        messages.error(request, f"No se pudo vincular: {str(exc)[:200]}")
    return redirect("alegra_contactos_catalogo")


@alegra_contact_change_required
@require_POST
def alegra_contacto_ignorar(request, staging_id):
    try:
        AlegraContactImporter().ignore_contact(staging_id, actor=request.user)
        messages.success(request, "Contacto marcado como ignorado.")
    except Exception as exc:
        messages.error(request, f"No se pudo ignorar: {str(exc)[:200]}")
    return redirect("alegra_contactos_catalogo")

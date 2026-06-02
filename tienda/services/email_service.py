import logging
from email.utils import formataddr, parseaddr

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


def remitente_betta():
    nombre = getattr(settings, "BETTA_EMAIL_FROM_NAME", "Betta Diseño")
    configurado = getattr(settings, "DEFAULT_FROM_EMAIL", "") or getattr(settings, "SERVER_EMAIL", "")
    nombre_configurado, email = parseaddr(configurado)
    if email:
        nombre_final = nombre or nombre_configurado
        return formataddr((nombre_final, email)) if nombre_final else email
    return configurado or None


def asunto_betta(asunto):
    nombre = getattr(settings, "BETTA_EMAIL_FROM_NAME", "Betta Diseño").strip()
    asunto = (asunto or "").strip()
    if not nombre or asunto.endswith(f"| {nombre}"):
        return asunto
    return f"{asunto} | {nombre}"


def enviar_correo_cliente(destinatarios, asunto, template_html, contexto=None, template_texto=None):
    if isinstance(destinatarios, str):
        destinatarios = [destinatarios]
    destinatarios = [email for email in destinatarios if email]
    if not destinatarios:
        return False

    contexto = contexto or {}
    contexto.setdefault("site_url", getattr(settings, "SITE_URL", ""))
    contexto.setdefault("from_name", getattr(settings, "BETTA_EMAIL_FROM_NAME", "Betta Diseño"))

    try:
        html = render_to_string(template_html, contexto)
        texto = render_to_string(template_texto, contexto) if template_texto else strip_tags(html)
        mensaje = EmailMultiAlternatives(asunto_betta(asunto), texto or asunto, remitente_betta(), destinatarios)
        mensaje.attach_alternative(html, "text/html")
        mensaje.send(fail_silently=False)
        return True
    except Exception:
        logger.exception("No se pudo enviar correo transaccional a cliente.")
        return False


def enviar_confirmacion_registro_cliente(cliente_usuario):
    return enviar_correo_cliente(
        cliente_usuario.user.email,
        "Bienvenido al portal de clientes",
        "tienda/emails/cliente_bienvenida.html",
        {
            "cliente_usuario": cliente_usuario,
            "cliente": cliente_usuario.cliente,
            "preview_text": "Tu acceso al portal de clientes Betta ya está activo.",
        },
    )


def enviar_confirmacion_solicitud(solicitud, destinatarios):
    return enviar_correo_cliente(
        destinatarios,
        f"Solicitud OP-{solicitud.id:06d} recibida",
        "tienda/emails/confirmacion_solicitud.html",
        {
            "solicitud": solicitud,
            "cliente": solicitud.cliente,
            "preview_text": "Hola, recibimos tu solicitud correctamente.",
        },
    )


def enviar_notificacion_cliente(notificacion):
    return enviar_correo_cliente(
        notificacion.cliente_usuario.user.email,
        notificacion.titulo,
        "tienda/emails/notificacion_cliente.html",
        {
            "notificacion": notificacion,
            "cliente": notificacion.cliente,
            "preview_text": "Tienes una actualización disponible en tu portal de clientes.",
        },
    )


def enviar_notificacion_estado_pedido(solicitud, cliente_usuario, mensaje):
    return enviar_correo_cliente(
        cliente_usuario.user.email,
        "Actualización de tu pedido",
        "tienda/emails/notificacion_cliente.html",
        {
            "cliente": cliente_usuario.cliente,
            "preview_text": f"El pedido OP-{solicitud.id:06d} tiene una actualización.",
            "notificacion": {
                "titulo": f"Pedido OP-{solicitud.id:06d}",
                "mensaje": mensaje,
                "url_destino": f"/cliente/pedidos/{solicitud.id}/",
            },
        },
    )


def enviar_notificacion_estado_proyecto(proyecto, cliente_usuario, mensaje):
    return enviar_correo_cliente(
        cliente_usuario.user.email,
        f"Actualización de proyecto {proyecto.nombre}",
        "tienda/emails/notificacion_cliente.html",
        {
            "cliente": cliente_usuario.cliente,
            "preview_text": "Tu proyecto tiene una actualización.",
            "notificacion": {
                "titulo": proyecto.nombre,
                "mensaje": mensaje,
                "url_destino": f"/cliente/proyectos/{proyecto.id}/",
            },
        },
    )


def enviar_notificacion_facturacion(solicitud, cliente_usuario, mensaje):
    return enviar_notificacion_estado_pedido(solicitud, cliente_usuario, mensaje)


def enviar_notificacion_documento_visible(solicitud, cliente_usuario, mensaje):
    return enviar_notificacion_estado_pedido(solicitud, cliente_usuario, mensaje)


def enviar_notificacion_novedad_visible(solicitud, cliente_usuario, mensaje):
    return enviar_notificacion_estado_pedido(solicitud, cliente_usuario, mensaje)

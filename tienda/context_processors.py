import re
from urllib.parse import quote

from django.conf import settings


def whatsapp_url():
    number = re.sub(r"\D+", "", getattr(settings, "BETTA_WHATSAPP_NUMBER", "") or "")
    if not number:
        return ""
    message = getattr(settings, "BETTA_WHATSAPP_MESSAGE", "") or ""
    url = f"https://wa.me/{number}"
    if message:
        url = f"{url}?text={quote(message)}"
    return url


def betta_global_context(request):
    cliente_unread = 0
    empleado_unread = 0
    if getattr(request, "user", None) and request.user.is_authenticated:
        cliente_usuario = getattr(request.user, "cliente_usuario", None)
        if cliente_usuario and cliente_usuario.activo:
            cliente_unread = cliente_usuario.notificaciones.filter(leida=False).count()
        if request.user.is_staff or hasattr(request.user, "empleado_perfil"):
            empleado_unread = request.user.notificaciones.filter(leida=False).count()

    return {
        "betta_whatsapp_url": whatsapp_url(),
        "cliente_unread_notifications_count": cliente_unread,
        "empleado_unread_notifications_count": empleado_unread,
    }

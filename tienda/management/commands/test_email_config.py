from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tienda.services.email_service import remitente_betta


def mask_email(value):
    if not value:
        return "-"
    email = value
    if "<" in value and ">" in value:
        email = value.split("<", 1)[1].split(">", 1)[0]
    if "@" not in email:
        return "configurado"
    local, domain = email.split("@", 1)
    return f"{local[:1] or '*'}***@{domain}"


class Command(BaseCommand):
    help = "Envía un correo de prueba usando la configuración actual sin exponer credenciales."

    def add_arguments(self, parser):
        parser.add_argument("destinatario", help="Correo destino de la prueba.")

    def handle(self, *args, **options):
        destinatario = options["destinatario"]
        remitente = remitente_betta()

        self.stdout.write(f"Backend: {settings.EMAIL_BACKEND}")
        self.stdout.write(f"Host: {getattr(settings, 'EMAIL_HOST', '-')}")
        self.stdout.write(f"Puerto: {getattr(settings, 'EMAIL_PORT', '-')}")
        self.stdout.write(f"TLS: {getattr(settings, 'EMAIL_USE_TLS', False)}")
        self.stdout.write(f"SSL: {getattr(settings, 'EMAIL_USE_SSL', False)}")
        self.stdout.write(f"Timeout: {getattr(settings, 'EMAIL_TIMEOUT', '-')}")
        self.stdout.write(f"Usuario SMTP: {mask_email(getattr(settings, 'EMAIL_HOST_USER', ''))}")
        self.stdout.write(
            f"Contraseña SMTP: {'configurada' if getattr(settings, 'EMAIL_HOST_PASSWORD', '') else 'no configurada'}"
        )
        self.stdout.write(f"Remitente: {mask_email(remitente)}")
        self.stdout.write(f"Destino: {mask_email(destinatario)}")

        fecha = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S %Z")
        texto = (
            "Correo de prueba de configuración Betta.\n\n"
            f"Fecha: {fecha}\n"
            "Si recibiste este mensaje, la configuración saliente está funcionando."
        )
        html = (
            '<div style="font-family:Arial,sans-serif;color:#172033;line-height:1.5">'
            '<h1 style="font-size:20px;margin:0 0 12px">Prueba de correo Betta</h1>'
            "<p>Si recibiste este mensaje, la configuración saliente está funcionando.</p>"
            f'<p style="color:#6b7280;font-size:13px">Fecha: {fecha}</p>'
            "</div>"
        )
        mensaje = EmailMultiAlternatives("Prueba de correo Betta", texto, remitente, [destinatario])
        mensaje.attach_alternative(html, "text/html")

        try:
            enviados = mensaje.send(fail_silently=False)
        except Exception as exc:
            raise CommandError(
                f"No se pudo enviar el correo de prueba ({exc.__class__.__name__}). "
                "Revisa host, puerto, TLS/SSL, usuario y contraseña de aplicación."
            ) from exc

        self.stdout.write(self.style.SUCCESS(f"Correos enviados: {enviados}"))
        if settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend":
            self.stdout.write(self.style.WARNING("Backend de consola activo: la prueba no salió por SMTP real."))

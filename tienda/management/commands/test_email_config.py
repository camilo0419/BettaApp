from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Envía un correo de prueba usando la configuración actual sin exponer credenciales."

    def add_arguments(self, parser):
        parser.add_argument("destinatario", help="Correo destino de la prueba.")

    def handle(self, *args, **options):
        destinatario = options["destinatario"]
        self.stdout.write(f"Backend: {settings.EMAIL_BACKEND}")
        self.stdout.write(f"Host: {getattr(settings, 'EMAIL_HOST', '-')}")
        self.stdout.write(f"Puerto: {getattr(settings, 'EMAIL_PORT', '-')}")
        self.stdout.write(f"TLS: {getattr(settings, 'EMAIL_USE_TLS', False)}")
        self.stdout.write(f"Usuario configurado: {'si' if getattr(settings, 'EMAIL_HOST_USER', '') else 'no'}")

        try:
            enviados = send_mail(
                "Prueba Betta",
                "Correo de prueba de configuración Betta.",
                None,
                [destinatario],
                fail_silently=False,
            )
        except Exception as exc:
            raise CommandError(f"No se pudo enviar el correo de prueba: {exc}") from exc

        self.stdout.write(self.style.SUCCESS(f"Correos enviados: {enviados}"))

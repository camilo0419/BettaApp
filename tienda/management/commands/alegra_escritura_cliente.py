"""Preparación y ejecución excepcional de un único POST de contacto Alegra."""

from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError

from tienda.models import Cliente, ExternalSystem
from tienda.services.alegra_write import (
    AlegraContactWriteService,
    AlegraWriteClient,
    ExternalWriteDisabled,
    WriteConflict,
    authorize_external_write,
    build_contact_payload,
)


CONFIRMATION = "AUTORIZAR UN SOLO POST ALEGRA"


class Command(BaseCommand):
    help = "Prepara o ejecuta, con autorización explícita, un único POST de contacto a Alegra."

    def add_arguments(self, parser):
        parser.add_argument("--client-id", type=int, required=True)
        parser.add_argument("--execute", action="store_true", help="Ejecuta exactamente un POST; por defecto solo dry-run.")
        parser.add_argument("--confirm", default="", help=f'Requerido con --execute: {CONFIRMATION}')
        parser.add_argument("--timeout", type=float, default=None)

    def handle(self, *args, **options):
        client = Cliente.objects.filter(pk=options["client_id"]).first()
        if not client:
            raise CommandError("Cliente local inexistente.")
        system = ExternalSystem.objects.filter(code="alegra", status=ExternalSystem.STATUS_ACTIVE).first()
        if not system:
            raise CommandError("Sistema Alegra local no disponible.")
        transport = AlegraWriteClient(timeout=options["timeout"])
        # No se usa el nombre como criterio suficiente para autorizar un POST.
        candidates = transport.find_candidates(identification=client.identificacion)
        if candidates:
            raise CommandError("Ejecución bloqueada: existen candidatos externos; requiere revisión manual.")
        try:
            payload = build_contact_payload(client)
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc
        if not options["execute"]:
            self.stdout.write(self.style.WARNING(
                f"DRY-RUN: cliente {client.pk}; candidatos=0; campos_payload={sorted(payload)}; ningún cambio persistido ni enviado."
            ))
            return
        if options["confirm"] != CONFIRMATION:
            raise CommandError(f'--execute requiere --confirm "{CONFIRMATION}".')
        service = AlegraContactWriteService(transport=transport)
        try:
            operation, _ = service.prepare_create(client, system)
            authorization = authorize_external_write(
                client_id=client.pk, operation="POST", environment=system.environment, confirmed=True,
            )
            result = service.execute_create(operation.pk, authorization=authorization)
        except (ExternalWriteDisabled, WriteConflict) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"POST único procesado: operación={result.pk}; estado={result.state}; http_status={transport.last_status}; external_id_present={bool(result.external_id)}"
        ))

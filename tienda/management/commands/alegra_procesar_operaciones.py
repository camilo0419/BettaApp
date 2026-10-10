"""Inspecciona la cola saliente; la ejecución externa está bloqueada por defecto."""

import os

from django.core.management.base import BaseCommand, CommandError

from tienda.models import AlegraWriteOperation
from tienda.services.alegra_operation_queue import (
    enqueue_missing_creates,
    linked_client_update_candidates,
    process_pending_operations,
)
from tienda.services.database_lock import DatabaseLockUnavailable, advisory_lock


PROCESS_LOCK = "bettaapp:alegra:outbound:contacts"


class Command(BaseCommand):
    help = "Prepara/procesa operaciones salientes de Alegra en lotes; dry-run por defecto."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--timeout", type=float, default=None)
        parser.add_argument("--execute", action="store_true", help="Reservado para habilitación explícita posterior.")
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options):
        try:
            with advisory_lock(PROCESS_LOCK):
                return self._handle_locked(options)
        except DatabaseLockUnavailable as exc:
            raise CommandError(str(exc)) from exc

    def _handle_locked(self, options):
        if options["execute"]:
            if options["confirm"] != "AUTORIZAR AUTOMATIZACION ALEGRA":
                raise CommandError("--execute requiere confirmación explícita.")
            if os.environ.get("ALEGRA_AUTOMATION_WRITES_ENABLED", "").casefold() != "true":
                raise CommandError("La automatización de escrituras permanece deshabilitada.")
            if os.environ.get("ALEGRA_EXTERNAL_WRITES_ENABLED", "").casefold() != "true":
                raise CommandError("Las escrituras externas permanecen deshabilitadas.")
        created = AlegraWriteOperation.objects.filter(state=AlegraWriteOperation.STATE_PENDING).count()
        queued = enqueue_missing_creates(limit=options["limit"], timeout=options["timeout"]) if options["execute"] else []
        created = AlegraWriteOperation.objects.filter(state=AlegraWriteOperation.STATE_PENDING).count()
        self.stdout.write(f"Operaciones pendientes: {created}; altas locales encoladas: {len(queued)}. Modo dry-run; no se ejecutaron escrituras.")
        try:
            result = linked_client_update_candidates(
                limit=options["limit"], timeout=options["timeout"], persist=options["execute"],
            )
        except Exception as exc:
            self.stdout.write(f"Preparación de actualizaciones bloqueada: {exc.__class__.__name__}.")
            return
        self.stdout.write(f"Actualizaciones preparadas: {len(result['prepared'])}; bloqueadas: {len(result['blocked'])}.")
        if options["execute"]:
            processed = process_pending_operations(
                limit=options["limit"], timeout=options["timeout"], execute=True,
            )
            self.stdout.write(f"Procesadas: {processed['results']}.")
        else:
            self.stdout.write("Modo dry-run; las escrituras externas siguen deshabilitadas.")

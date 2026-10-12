"""Inspecciona la cola saliente; la ejecución externa está bloqueada por defecto."""

import os
from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from tienda.models import AlegraWriteOperation
from tienda.services.alegra_operation_queue import (
    enqueue_missing_creates,
    linked_client_update_candidates,
    OUTBOUND_PROCESS_LOCK,
    process_pending_operations,
)
from tienda.services.database_lock import DatabaseLockUnavailable, advisory_lock


PROCESS_LOCK = OUTBOUND_PROCESS_LOCK


class Command(BaseCommand):
    help = "Prepara/procesa operaciones salientes de Alegra en lotes; dry-run por defecto."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--timeout", type=float, default=None)
        parser.add_argument("--execute", action="store_true", help="Reservado para habilitación explícita posterior.")
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options):
        return self._handle(options)

    def _handle(self, options):
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
        mode = "ejecución" if options["execute"] else "dry-run"
        suffix = "" if options["execute"] else "; no se ejecutaron escrituras"
        self.stdout.write(f"Operaciones pendientes: {created}; altas locales encoladas: {len(queued)}. Modo {mode}{suffix}.")
        try:
            result = linked_client_update_candidates(
                limit=options["limit"], timeout=options["timeout"], persist=options["execute"],
            )
        except Exception as exc:
            self.stdout.write(f"Preparación de actualizaciones bloqueada: {exc.__class__.__name__}.")
            return
        self.stdout.write(f"Actualizaciones preparadas: {len(result['prepared'])}; bloqueadas: {len(result['blocked'])}.")
        reasons = Counter(item.get("reason", "other") for item in result.get("diagnostics", []))
        self.stdout.write(
            "Diagnóstico: "
            f"sin_cambios={reasons.get('no_changes', 0)}; "
            f"falta_baseline={reasons.get('missing_baseline', 0)}; "
            f"conflictos={reasons.get('conflict', 0)}; "
            f"errores_consulta={sum(value for key, value in reasons.items() if key.startswith('query_error:'))}; "
            f"vinculos_invalidos={reasons.get('invalid_mapping', 0) + reasons.get('duplicate_active_maps', 0)}; "
            f"otros={sum(value for key, value in reasons.items() if key not in {'no_changes', 'missing_baseline', 'conflict', 'invalid_mapping', 'duplicate_active_maps'} and not key.startswith('query_error:'))}."
        )
        if options["execute"]:
            try:
                # El bloqueo global cubre únicamente la fase que puede ejecutar
                # POST/PUT. Los recorridos y GET preparatorios ya terminaron.
                with advisory_lock(PROCESS_LOCK):
                    processed = process_pending_operations(
                        limit=options["limit"], timeout=options["timeout"], execute=True,
                    )
            except DatabaseLockUnavailable:
                self.stdout.write("Procesamiento omitido: otra ejecución está procesando operaciones.")
            else:
                self.stdout.write(f"Procesadas: {processed['results']}.")
        else:
            self.stdout.write("Modo dry-run; las escrituras externas siguen deshabilitadas.")

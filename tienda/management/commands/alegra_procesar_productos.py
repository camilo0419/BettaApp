"""Procesa la cola saliente de productos; dry-run por defecto."""

import os

from django.core.management.base import BaseCommand, CommandError

from tienda.models import AlegraProductWriteOperation
from tienda.services.alegra_product_write import process_pending_product_operations


CONFIRMATION = "AUTORIZAR ESCRITURAS DE PRODUCTOS ALEGRA"


class Command(BaseCommand):
    help = "Inspecciona/procesa operaciones salientes de productos Alegra; dry-run por defecto."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--timeout", type=float, default=None)
        parser.add_argument("--operation-id", type=int, default=None, help="Procesa únicamente esta operación pendiente.")
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options):
        execute = options["execute"]
        operation_id = options["operation_id"]
        if operation_id is not None:
            try:
                operation = AlegraProductWriteOperation.objects.get(pk=operation_id)
            except AlegraProductWriteOperation.DoesNotExist as exc:
                raise CommandError(f"No existe la operación de producto {operation_id}.") from exc
            if operation.state != AlegraProductWriteOperation.STATE_PENDING:
                raise CommandError(
                    f"La operación {operation_id} no está pendiente (estado actual: {operation.state})."
                )
        if execute:
            if options["confirm"] != CONFIRMATION:
                raise CommandError(f'--execute requiere --confirm "{CONFIRMATION}".')
            if os.environ.get("ALEGRA_AUTOMATION_WRITES_ENABLED", "").casefold() != "true":
                raise CommandError("ALEGRA_AUTOMATION_WRITES_ENABLED no está habilitada.")
            if os.environ.get("ALEGRA_EXTERNAL_WRITES_ENABLED", "").casefold() != "true":
                raise CommandError("ALEGRA_EXTERNAL_WRITES_ENABLED no está habilitada.")
        result = process_pending_product_operations(
            limit=options["limit"], timeout=options["timeout"], execute=execute,
            operation_id=operation_id,
        )
        self.stdout.write(
            f"Modo={'execute' if execute else 'dry-run'}; pendientes={result['pending']}; "
            f"resultados={len(result['results'])}."
        )
        if not execute:
            self.stdout.write("No se realizaron escrituras externas.")

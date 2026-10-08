"""Procesa trabajos UNSPSC fuera de las peticiones HTTP, apto para cron."""

import json
import re
from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from tienda.models import UNSPSCImportJob


CONFIRMATION = "APLICAR IMPORTACIÓN UNSPSC"


class Command(BaseCommand):
    help = "Valida o aplica un trabajo UNSPSC pendiente; pensado para cron."

    def add_arguments(self, parser):
        parser.add_argument("--job-id", type=int)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options):
        queryset = UNSPSCImportJob.objects.filter(
            status=UNSPSCImportJob.STATUS_APPLY_REQUESTED if options["apply"] else UNSPSCImportJob.STATUS_PENDING,
        ).order_by("created_at")
        if options.get("job_id"):
            queryset = queryset.filter(pk=options["job_id"])
        job = queryset.first()
        if not job:
            self.stdout.write("No hay trabajos UNSPSC pendientes.")
            return
        if options["apply"] and options["confirm"] != CONFIRMATION:
            raise CommandError(f'--apply requiere --confirm "{CONFIRMATION}".')
        with transaction.atomic():
            job = UNSPSCImportJob.objects.select_for_update().get(pk=job.pk)
            job.status = UNSPSCImportJob.STATUS_PROCESSING
            job.started_at = timezone.now()
            job.error_message = ""
            job.save(update_fields=["status", "started_at", "error_message"])
        output = StringIO()
        try:
            command_options = {
                "file": job.file.path,
                "catalog_version": job.catalog_version,
                "source_url": job.source_url,
                "batch_size": 1000,
                "apply": bool(options["apply"]),
                "confirm": "IMPORTAR CATÁLOGO UNSPSC" if options["apply"] else "",
            }
            call_command("unspsc_importar", stdout=output, **command_options)
            text = output.getvalue()
            match = re.search(r"filas_fuente=(\d+).*códigos_únicos=(\d+).*repeticiones_jerarquía=(\d+).*inválidos=(\d+).*niveles=(\{.*?\})", text)
            preview = {"output": text[-2000:]}
            if match:
                preview["levels"] = match.group(5)
                values = {"source_rows": int(match.group(1)), "unique_codes": int(match.group(2)), "duplicate_rows": int(match.group(3)), "invalid_rows": int(match.group(4)), "preview": preview}
            else:
                values = {"preview": preview}
            values["status"] = UNSPSCImportJob.STATUS_COMPLETED if options["apply"] else UNSPSCImportJob.STATUS_READY
            values["completed_at"] = timezone.now()
            UNSPSCImportJob.objects.filter(pk=job.pk).update(**values)
            self.stdout.write(text)
        except Exception as exc:
            UNSPSCImportJob.objects.filter(pk=job.pk).update(status=UNSPSCImportJob.STATUS_FAILED, error_message=str(exc)[:500], completed_at=timezone.now())
            raise

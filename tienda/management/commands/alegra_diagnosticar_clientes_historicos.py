"""Diagnóstico de clientes históricos sin vínculo; solo lectura."""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from tienda.models import Cliente, ExternalSystem
from tienda.services.alegra_contact_import import normalize_identity
from tienda.services.alegra_normalization import extract_identification_context
from tienda.services.alegra_write import AlegraWriteClient, COLOMBIA_ID_TYPES


class Command(BaseCommand):
    help = "Diagnostica candidatos Alegra para clientes históricos sin vínculo; nunca modifica datos."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Incluye todos los clientes históricos.")
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--timeout", type=float, default=None)

    def handle(self, *args, **options):
        if not ExternalSystem.objects.filter(code="alegra").exists():
            raise CommandError("No existe configuración local de Alegra; no se realizó ninguna consulta.")

        queryset = Cliente.objects.filter(
            alegra_sync_policy=Cliente.ALEGRA_SYNC_HISTORICAL_PENDING,
        ).order_by("pk")
        if not options["all"]:
            queryset = queryset[: max(int(options["limit"]), 1)]

        client = AlegraWriteClient(timeout=options["timeout"])
        counts = Counter()
        processed = 0
        for local in queryset:
            processed += 1
            expected_type = COLOMBIA_ID_TYPES.get(local.tipo_identificacion, "")
            try:
                candidates = client.find_candidates(identification=normalize_identity(local.identificacion))
            except Exception as exc:
                counts[f"error_{exc.__class__.__name__}"] += 1
                continue

            local_number = normalize_identity(local.identificacion)
            exact_number = []
            exact_document = []
            for row in candidates:
                identity = extract_identification_context(row)
                remote_number = normalize_identity(identity.get("number", ""))
                if remote_number != local_number:
                    continue
                exact_number.append(row)
                remote_type = str(identity.get("type", "")).strip().upper()
                if expected_type and remote_type == expected_type:
                    exact_document.append(row)

            if len(exact_document) == 1:
                counts["inequivoca_identificacion_y_documento"] += 1
            elif len(exact_document) > 1:
                counts["ambigua_multiples_identificacion_y_documento"] += 1
            elif exact_number:
                counts["diferencia_tipo_documento"] += 1
            else:
                counts["sin_coincidencia"] += 1

        self.stdout.write(f"Modo=dry-run; procesados={processed}; sin escrituras externas.")
        self.stdout.write("Desglose agregado (sin datos personales):")
        for reason, amount in sorted(counts.items()):
            self.stdout.write(f"- {reason}: {amount}")


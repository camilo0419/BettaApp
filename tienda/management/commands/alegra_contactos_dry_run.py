"""Inventario completo de contactos sin importar ni modificar clientes."""

from collections import Counter

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError

from tienda.models import Cliente, ExternalObjectMap, ExternalSystem
from tienda.services.alegra_client import AlegraError, AlegraReadOnlyClient
from tienda.services.alegra_preimport_clients import fetch_customer_contacts


class Command(BaseCommand):
    help = "Consulta todos los contactos cliente de Alegra y reporta cobertura sin cambios locales."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Requerido para cobertura exhaustiva.")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--max-pages", type=int, default=10)
        parser.add_argument("--pause", type=float, default=0)
        parser.add_argument("--timeout", type=float, default=None)
        parser.add_argument("--max-retries", type=int, default=2)

    def handle(self, *args, **options):
        if not options["all"]:
            raise CommandError("Usa --all para solicitar cobertura completa; el modo limitado es explícito.")
        try:
            fetched = fetch_customer_contacts(
                AlegraReadOnlyClient(timeout=options["timeout"]), limit=None, max_pages=None,
                pause=options["pause"], max_retries=options["max_retries"],
            )
        except AlegraError as exc:
            raise CommandError(str(exc)) from exc
        system = ExternalSystem.objects.filter(code="alegra").first()
        ct = ContentType.objects.get_for_model(Cliente)
        mapped = set(ExternalObjectMap.objects.filter(
            system=system, resource_type="contacts", content_type=ct,
            status=ExternalObjectMap.STATUS_ACTIVE,
        ).values_list("external_id", flat=True)) if system else set()
        ids = [str(row.get("id") or "") for row in fetched["rows"]]
        incomplete = sum(1 for row in fetched["rows"] if not row.get("id") or not row.get("name"))
        self.stdout.write(self.style.SUCCESS(
            f"Contactos: total={len(fetched['rows'])}, ids_unicos={len(set(ids))}, "
            f"paginas={fetched['pages']}, duplicados={fetched['duplicate_external_ids']}, "
            f"incompletos={incomplete}, mapeados={len(set(ids) & mapped)}, "
            f"sin_mapeo={len(set(ids) - mapped)}, cobertura={fetched['coverage']}, "
            f"final={fetched['stopped_reason']}, errores={len(fetched['errors'])}. "
            "No se modificaron clientes, staging ni mapeos."
        ))

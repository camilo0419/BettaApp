"""Consulta y concilia productos Alegra sin crear productos comerciales."""

from django.core.management.base import BaseCommand, CommandError

from tienda.services.alegra_client import AlegraError, AlegraReadOnlyClient
from tienda.services.alegra_import import AlegraItemImporter


class Command(BaseCommand):
    help = "Consulta /items, actualiza staging y genera una conciliación sin importar productos."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Recorre hasta el final verificable; no usa límite de catálogo.")
        parser.add_argument("--limit", type=int, default=100, help="Límite diagnóstico; se ignora con --all.")
        parser.add_argument("--max-pages", type=int, default=10, help="Límite diagnóstico; se ignora con --all.")
        parser.add_argument("--pause", type=float, default=0)
        parser.add_argument("--timeout", type=float, default=None)

    def handle(self, *args, **options):
        limit = None if options["all"] else min(max(options["limit"], 1), 1000)
        max_pages = None if options["all"] else min(max(options["max_pages"], 1), 100)
        try:
            result = AlegraItemImporter().sync(
                limit=limit, max_pages=max_pages, pause=options["pause"],
                timeout=options["timeout"],
            )
        except AlegraError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                "DRY-RUN productos: "
                f"consultados={result['total']}, creados_staging={result['created']}, "
                f"actualizados_staging={result['updated']}, conflictos={result['conflicts']}, "
                f"paginas={result['pages']}, duplicados={result['duplicate_external_ids']}, "
                f"completo={result['complete']}, errores={len(result['errors'])}. "
                "No se crearon ni actualizaron productos comerciales y no hubo escrituras externas."
            )
        )

"""Concilia operaciones sent/inciertas de productos usando únicamente GET."""

from django.core.management.base import BaseCommand, CommandError

from tienda.services.alegra_product_write import reconcile_product_operations


CONFIRMATION = "CONCILIAR OPERACIONES DE PRODUCTOS ALEGRA"


class Command(BaseCommand):
    help = "Concilia operaciones de productos sent/inciertas; dry-run por defecto y solo GET."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--max-pages", type=int)
        parser.add_argument("--timeout", type=float, default=None)
        parser.add_argument("--apply", action="store_true", help="Solo aplica cambios locales de conciliación.")
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options):
        if options["apply"] and options["confirm"] != CONFIRMATION:
            raise CommandError(f'--apply requiere --confirm "{CONFIRMATION}".')
        result = reconcile_product_operations(
            limit=options["limit"], timeout=options["timeout"],
            max_pages=options["max_pages"], apply=options["apply"],
        )
        self.stdout.write(
            f"Modo={result['mode']}; operaciones={result['processed']}; "
            f"resultados={len(result['results'])}. Solo GET externo."
        )
        for row in result["results"]:
            self.stdout.write(f"Operación {row['id']}: {row['status']}.")

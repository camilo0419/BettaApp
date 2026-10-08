"""Importador controlado de staging a productos comerciales.

El comando es dry-run por defecto. La importación efectiva requiere decisión
explícita; categoría y tipo de cálculo pueden completarse posteriormente
desde el panel. Nunca importa inventario ni sobrescribe precios comerciales.
"""

import sqlite3
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from tienda.models import AlegraItemStaging, Producto
from tienda.services.alegra_import import get_alegra_system, AlegraItemImporter


CONFIRMATION = "IMPORTAR PRODUCTOS LOCALES"


def sqlite_backup_path():
    folder = Path(tempfile.gettempdir()) / "bettaapp-backups"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"db.sqlite3.productos-{__import__('datetime').datetime.now():%Y%m%d-%H%M%S}.bak"


def backup_sqlite_if_local():
    database = settings.DATABASES["default"]
    if "sqlite3" not in str(database.get("ENGINE", "")):
        return None
    source = Path(str(database["NAME"])).resolve()
    destination = sqlite_backup_path()
    source_conn = sqlite3.connect(str(source))
    target_conn = sqlite3.connect(str(destination))
    try:
        source_conn.backup(target_conn)
        target_conn.commit()
    finally:
        target_conn.close()
        source_conn.close()
    if not destination.exists() or destination.stat().st_size <= 0:
        raise CommandError("El respaldo SQLite no pudo verificarse.")
    check = sqlite3.connect(str(destination))
    try:
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if integrity != "ok":
        raise CommandError("El respaldo SQLite no superó integrity_check.")
    return destination


class Command(BaseCommand):
    help = "Concilia/importa productos Alegra desde staging; dry-run por defecto."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm", default="")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--category-id", type=int)
        parser.add_argument("--calculation-type", choices=[key for key, _ in Producto.CALCULO_CHOICES])

    def handle(self, *args, **options):
        system = get_alegra_system()
        eligible = list(AlegraItemStaging.objects.filter(
            system=system, classification=AlegraItemStaging.CLASS_NEW,
            matched_product__isnull=True, imported_product__isnull=True,
        ).order_by("pk")[: min(max(options["limit"], 1), 1000)])
        blocked = AlegraItemStaging.objects.filter(system=system).exclude(
            classification=AlegraItemStaging.CLASS_NEW,
        ).count()
        self.stdout.write(
            f"DRY-RUN catálogo: elegibles={len(eligible)}, bloqueados/revisión={blocked}, "
            "sin inventario ni sincronización de precios."
        )
        if not options["apply"]:
            self.stdout.write("No se modificaron productos; usa --apply con confirmación explícita para una ejecución futura.")
            return
        if options["confirm"] != CONFIRMATION:
            raise CommandError(f'--apply requiere --confirm "{CONFIRMATION}".')
        # Ambos datos pueden quedar pendientes; la interfaz administrativa los
        # completará después. Si se informan, se validan por el servicio.
        backup = backup_sqlite_if_local()
        importer = AlegraItemImporter()
        created, errors = 0, 0
        for staging in eligible:
            try:
                with transaction.atomic():
                    importer.import_item(
                        staging.pk, category_id=options["category_id"],
                        calculation_type=options["calculation_type"],
                    )
                created += 1
            except Exception:
                errors += 1
        self.stdout.write(f"Importados={created}; errores={errors}; backup={backup or 'no aplica'}.")

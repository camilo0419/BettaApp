"""Valida e importa el catálogo UNSPSC oficial por lotes."""

import csv
import io
import re
import tempfile
import time
import unicodedata
import zipfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from tienda.models import SyncAuditLog, UNSPSCCode


CONFIRMATION = "IMPORTAR CATÁLOGO UNSPSC"
MAX_FILE_BYTES = 400 * 1024 * 1024
LEVEL_FIELDS = {
    "segment": ("Segmento", "Título del Segmento"),
    "family": ("Familia", "Título de Familia"),
    "class": ("Clase", "Título de Clase"),
    "product": ("Producto", "Título Producto"),
}


def _norm(value):
    value = unicodedata.normalize("NFKD", str(value or "").strip().casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", value)


def _code(value):
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    digits = re.sub(r"\D", "", text)
    if len(digits) == 7:
        digits = digits.zfill(8)
    return digits if len(digits) == 8 else ""


def _is_active(value):
    return str(value or "").strip().casefold() not in {"0", "no", "false", "inactivo", "deshabilitado", "disabled"}


def _source_file(path):
    path = Path(path).resolve()
    if not path.exists() or not path.is_file():
        raise CommandError("El archivo UNSPSC no existe o no es un archivo regular.")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise CommandError("El archivo UNSPSC supera el límite permitido de 400 MB.")
    if path.suffix.casefold() != ".zip":
        return path
    with zipfile.ZipFile(path) as archive:
        files = [info for info in archive.infolist() if not info.is_dir() and Path(info.filename).suffix.casefold() in {".csv", ".xlsx"}]
        if len(files) != 1:
            raise CommandError("El ZIP debe contener exactamente un CSV o XLSX de catálogo.")
        if files[0].file_size > MAX_FILE_BYTES or files[0].compress_size == 0 or files[0].file_size / max(files[0].compress_size, 1) > 1000:
            raise CommandError("El ZIP no supera las validaciones de tamaño o compresión.")
        target = Path(tempfile.gettempdir()) / f"unspsc-source-{time.time_ns()}{Path(files[0].filename).suffix.casefold()}"
        with target.open("wb") as handle:
            handle.write(archive.read(files[0]))
        return target


def _iter_csv(path, catalog_version, source):
    raw_handle = path.open("rb")
    try:
        probe = raw_handle.read(65536)
        raw_handle.seek(0)
        try:
            probe.decode("utf-8-sig")
            encoding = "utf-8-sig"
        except UnicodeDecodeError:
            encoding = "cp1252"
        with io.TextIOWrapper(raw_handle, encoding=encoding, newline="") as handle:
            sample = handle.read(8192)
            handle.seek(0)
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=";,\t").delimiter
            except csv.Error:
                delimiter = ";"
            reader = csv.DictReader(handle, delimiter=delimiter)
            headers = reader.fieldnames or []
            normalized = {_norm(header): header for header in headers}
            if _norm("Producto") in normalized and _norm("Segmento") in normalized:
                for source_row, row in enumerate(reader, start=2):
                    for level, (code_field, title_field) in LEVEL_FIELDS.items():
                        code = _code(row.get(code_field))
                        title = str(row.get(title_field) or "").strip()
                        if code and title:
                            yield {"code": code, "description": title[:255], "level": level, "active": True, "catalog_version": catalog_version, "source": source, "source_row": source_row}
                return
            code_header = next((header for key, header in normalized.items() if key in {"codigo", "code", "codigounspsc", "unspsc"}), None)
            description_header = next((header for key, header in normalized.items() if key in {"descripcion", "description", "nombre", "producto"}), None)
            active_header = next((header for key, header in normalized.items() if key in {"vigente", "activo", "active", "estado", "status"}), None)
            if not code_header or not description_header:
                raise CommandError("No se identificaron columnas de código y descripción UNSPSC.")
            for source_row, row in enumerate(reader, start=2):
                code = _code(row.get(code_header))
                description = str(row.get(description_header) or "").strip()
                if not code or not description:
                    continue
                level = "segment" if code.endswith("000000") else "family" if code.endswith("0000") else "class" if code.endswith("00") else "product"
                yield {"code": code, "description": description[:255], "level": level, "active": _is_active(row.get(active_header)) if active_header else True, "catalog_version": catalog_version, "source": source, "source_row": source_row}
    finally:
        if not raw_handle.closed:
            raw_handle.close()


def _iter_rows(path, catalog_version, source):
    source_path = _source_file(path)
    try:
        if source_path.suffix.casefold() == ".csv":
            yield from _iter_csv(source_path, catalog_version, source)
            return
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise CommandError("El runtime no tiene openpyxl para leer el XLSX oficial.") from exc
        workbook = load_workbook(source_path, read_only=True, data_only=True)
        try:
            sheet = workbook.active
            values = sheet.iter_rows(values_only=True)
            headers = next(values, None)
            if not headers:
                return
            headers = [str(value or "").strip() for value in headers]
            normalized = {_norm(header): header for header in headers}
            code_header = next((header for key, header in normalized.items() if key in {"codigo", "code", "codigounspsc", "unspsc"}), None)
            description_header = next((header for key, header in normalized.items() if key in {"descripcion", "description", "nombre", "producto"}), None)
            if not code_header or not description_header:
                raise CommandError("No se identificaron columnas de código y descripción UNSPSC.")
            for source_row, values_row in enumerate(values, start=2):
                row = dict(zip(headers, values_row))
                code = _code(row.get(code_header))
                description = str(row.get(description_header) or "").strip()
                if not code or not description:
                    continue
                level = "segment" if code.endswith("000000") else "family" if code.endswith("0000") else "class" if code.endswith("00") else "product"
                yield {"code": code, "description": description[:255], "level": level, "active": True, "catalog_version": catalog_version, "source": source, "source_row": source_row}
        finally:
            workbook.close()
    finally:
        if source_path != Path(path).resolve() and source_path.exists():
            source_path.unlink(missing_ok=True)


def _parent_code(code):
    if code.endswith("000000"):
        return None
    if code.endswith("0000"):
        return code[:2] + "000000"
    if code.endswith("00"):
        return code[:4] + "0000"
    return code[:6] + "00"


class Command(BaseCommand):
    help = "Valida/importa un catálogo UNSPSC oficial; dry-run por defecto."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="Ruta local al CSV, XLSX o ZIP oficial.")
        parser.add_argument("--catalog-version", required=True, help="Versión efectiva declarada por la fuente.")
        parser.add_argument("--source-url", default="", help="URL oficial de origen.")
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options):
        batch_size = max(1, min(options["batch_size"], 5000))
        source = options["source_url"]
        seen = set()
        duplicates = invalid = rows = source_rows = 0
        last_source_row = None
        by_level = {key: 0 for key in LEVEL_FIELDS}
        started = time.monotonic()
        for record in _iter_rows(options["file"], options["catalog_version"], source):
            rows += 1
            if record.get("source_row") != last_source_row:
                source_rows += 1
                last_source_row = record.get("source_row")
            code = record["code"]
            if code in seen:
                duplicates += 1
                continue
            if len(code) != 8 or not code.isdigit():
                invalid += 1
                continue
            seen.add(code)
            by_level[record["level"]] += 1
        elapsed = round(time.monotonic() - started, 2)
        self.stdout.write(f"DRY-RUN UNSPSC: filas_fuente={source_rows}; filas_jerarquia={rows}; códigos_únicos={len(seen)}; repeticiones_jerarquía={duplicates}; inválidos={invalid}; niveles={by_level}; segundos={elapsed}.")
        if not options["apply"]:
            self.stdout.write("No se modificó la base. Usa --apply con confirmación explícita para cargar el catálogo.")
            return
        if options["confirm"] != CONFIRMATION:
            raise CommandError(f'--apply requiere --confirm "{CONFIRMATION}".')
        seen_apply = set()
        batch = []
        applied_batches = 0

        def flush(records):
            nonlocal applied_batches
            if not records:
                return
            with transaction.atomic():
                for record in records:
                    UNSPSCCode.objects.update_or_create(
                        catalog_version=record["catalog_version"], code=record["code"],
                        defaults={"description": record["description"], "level": record["level"], "active": record["active"], "source": record["source"]},
                    )
                for record in records:
                    parent_code = _parent_code(record["code"])
                    parent = UNSPSCCode.objects.filter(catalog_version=record["catalog_version"], code=parent_code).first() if parent_code else None
                    UNSPSCCode.objects.filter(catalog_version=record["catalog_version"], code=record["code"]).update(parent=parent)
            applied_batches += 1

        for record in _iter_rows(options["file"], options["catalog_version"], source):
            if record["code"] in seen_apply:
                continue
            seen_apply.add(record["code"])
            batch.append(record)
            if len(batch) >= batch_size:
                flush(batch)
                batch = []
        flush(batch)
        SyncAuditLog.objects.create(
            operation="import_unspsc_catalog", resource="unspsc", result=SyncAuditLog.RESULT_SUCCESS,
            detail=f"Catálogo UNSPSC {options['catalog_version']}: {len(seen)} códigos actualizados.",
            metadata={"version": options["catalog_version"], "count": len(seen), "duplicates": duplicates, "invalid": invalid, "source": source},
        )
        self.stdout.write(f"Importados/actualizados={len(seen_apply)}; lotes={applied_batches}.")

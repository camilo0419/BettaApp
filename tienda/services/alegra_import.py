"""Importación local, por lotes y de solo lectura desde Alegra."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from tienda.models import (
    AlegraItemStaging,
    ExternalObjectMap,
    ExternalSystem,
    Producto,
    SyncAuditLog,
)
from tienda.services.alegra_client import AlegraError, AlegraReadOnlyClient, extract_rows
from tienda.services.sync_freshness import sync_is_complete


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value).strip().casefold()


def _safe_text(value, max_length=None):
    text = str(value or "")
    return text[:max_length] if max_length else text


def _safe_decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _reference_price(item):
    prices = item.get("price") or []
    if isinstance(prices, dict):
        prices = [prices]
    if not isinstance(prices, list):
        return None
    ordered = sorted(prices, key=lambda row: not bool(row.get("main")) if isinstance(row, dict) else True)
    for row in ordered:
        if isinstance(row, dict):
            price = _safe_decimal(row.get("price"))
            if price is not None:
                return price
    return None


def _technical_data(item):
    """Allow-list technical fields; never persist headers, credentials or full payload."""
    keys = ("id", "type", "itemType", "status", "calculationScale", "category", "price", "tax", "inventory", "variantAttributes", "itemVariants")
    result = {}
    for key in keys:
        value = item.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
        elif isinstance(value, dict):
            result[key] = {str(k): v for k, v in list(value.items())[:30] if isinstance(v, (str, int, float, bool)) or v is None}
        elif isinstance(value, list):
            result[key] = value[:20]
    return result


def get_alegra_system():
    return ExternalSystem.objects.get_or_create(
        code="alegra",
        defaults={
            "name": "Alegra",
            "environment": ExternalSystem.ENVIRONMENT_PRODUCTION,
            "status": ExternalSystem.STATUS_ACTIVE,
            "config": {"api_version": "v1", "read_only": True},
        },
    )[0]


class AlegraItemImporter:
    resource_type = "items"

    def __init__(self, client=None):
        self.client = client

    def sync(self, *, limit=300, actor=None, max_pages=None, pause=0, timeout=None):
        """Lee /items en páginas y hace upsert idempotente en staging."""
        system = get_alegra_system()
        total = 0
        created = 0
        updated = 0
        conflicts = 0
        errors = []
        try:
            client = self.client or AlegraReadOnlyClient(timeout=timeout)
            page_kwargs = {"limit": limit, "params": {"mode": "advanced"}}
            # Mantiene compatibilidad con transportes de prueba y adaptadores
            # antiguos que solo implementaban limit/params.
            if max_pages is not None:
                page_kwargs["max_pages"] = max_pages
            if pause:
                page_kwargs["pause"] = pause
            responses = client.paged_get("/items", **page_kwargs)
            product_index = self._product_name_index()
            unique_items = {}
            duplicate_ids = set()
            for response in responses:
                for item in extract_rows(response.data):
                    external_id = _safe_text(item.get("id"), 120) if isinstance(item, dict) else ""
                    if external_id in unique_items:
                        duplicate_ids.add(external_id)
                        continue
                    if external_id:
                        unique_items[external_id] = item
                    result = self._upsert_item(system, item, product_index)
                    total += 1
                    created += result == "created"
                    updated += result == "updated"
                    conflicts += result == "conflict"
            classification = AlegraItemReconciler().classify(system=system, actor=actor)
            complete = self._is_complete(responses, limit, total)
            result = SyncAuditLog.RESULT_SUCCESS if complete else SyncAuditLog.RESULT_PARTIAL
            SyncAuditLog.objects.create(
                system=system,
                operation="sync_items",
                resource=self.resource_type,
                actor=actor,
                result=result,
                detail=f"Ítems procesados: {total}; nuevos: {created}; actualizados: {updated}; conflictos: {conflicts}.",
                metadata={"limit": limit, "pages": len(responses), "classification": classification, "complete": complete, "duplicate_external_ids": len(duplicate_ids)},
            )
        except AlegraError as exc:
            errors.append(str(exc))
            SyncAuditLog.objects.create(
                system=system,
                operation="sync_items",
                resource=self.resource_type,
                actor=actor,
                result=SyncAuditLog.RESULT_ERROR,
                detail=str(exc)[:500],
                metadata={"limit": limit, "complete": False},
            )
        return {"total": total, "created": created, "updated": updated, "conflicts": conflicts, "errors": errors, "complete": bool(not errors and 'complete' in locals() and complete), "pages": len(responses) if 'responses' in locals() else 0, "duplicate_external_ids": len(duplicate_ids) if 'duplicate_ids' in locals() else 0, "status": result if not errors else SyncAuditLog.RESULT_ERROR}

    @staticmethod
    def _is_complete(responses, requested_limit, processed_count):
        total = None
        for response in responses:
            metadata = response.data.get("metadata") if isinstance(response.data, dict) else None
            if isinstance(metadata, dict) and metadata.get("total") is not None:
                try:
                    total = int(metadata["total"])
                    break
                except (TypeError, ValueError):
                    pass
        if total is not None:
            return processed_count >= total
        if requested_limit is None:
            rows = extract_rows(responses[-1].data) if responses else []
            return bool(responses) and len(rows) < 30
        return processed_count < max(int(requested_limit), 1)

    @staticmethod
    def _product_name_index():
        index = {}
        for product in Producto.objects.only("id", "nombre").iterator(chunk_size=500):
            normalized = normalize_text(product.nombre)
            if normalized:
                index.setdefault(normalized, []).append(product)
        return index

    def _upsert_item(self, system, item, product_index=None):
        external_id = _safe_text(item.get("id"), 120)
        if not external_id:
            return "updated"
        name = _safe_text(item.get("name"), 150)
        reference = _safe_text(item.get("reference"), 120)
        category = item.get("category") if isinstance(item.get("category"), dict) else {}
        normalized_name = normalize_text(name)
        matches = (product_index or self._product_name_index()).get(normalized_name, []) if normalized_name else []
        mapped = ExternalObjectMap.objects.filter(system=system, resource_type=self.resource_type, external_id=external_id).first()
        imported = mapped.local_object if mapped and mapped.content_type_id and mapped.object_id else None
        if not name:
            review_status = AlegraItemStaging.REVIEW_ERROR
        elif imported:
            review_status = AlegraItemStaging.REVIEW_IMPORTED
        elif len(matches) == 1:
            review_status = AlegraItemStaging.REVIEW_MATCH
        elif len(matches) > 1:
            review_status = AlegraItemStaging.REVIEW_CONFLICT
        else:
            review_status = AlegraItemStaging.REVIEW_PENDING
        defaults = {
            "name": name or f"Ítem Alegra {external_id}",
            "reference": reference,
            "description": _safe_text(item.get("description"), 500),
            "external_category_id": _safe_text(category.get("id"), 120),
            "external_category_name": _safe_text(category.get("name"), 150),
            "reference_price": _reference_price(item),
            "external_status": _safe_text(item.get("status"), 30),
            "external_type": _safe_text(item.get("type") or item.get("itemType"), 40),
            "review_status": review_status,
            "matched_product": matches[0] if len(matches) == 1 else None,
            "imported_product": imported,
            "technical_data": _technical_data(item),
            "error_detail": (
                "Falta el nombre obligatorio del ítem externo."
                if review_status == AlegraItemStaging.REVIEW_ERROR
                else ("" if review_status != AlegraItemStaging.REVIEW_CONFLICT else "Más de un producto local coincide por nombre.")
            ),
            "fetched_at": timezone.now(),
        }
        staging = AlegraItemStaging.objects.filter(system=system, external_id=external_id).first()
        if staging and staging.classification_locked:
            for field in ("name", "reference", "description", "external_category_id", "external_category_name", "reference_price", "external_status", "external_type", "technical_data", "fetched_at"):
                setattr(staging, field, defaults[field])
            staging.save(update_fields=["name", "reference", "description", "external_category_id", "external_category_name", "reference_price", "external_status", "external_type", "technical_data", "fetched_at", "updated_at"])
            created = False
        else:
            staging, created = AlegraItemStaging.objects.update_or_create(
                system=system,
                external_id=external_id,
                defaults=defaults,
            )
        return "conflict" if review_status == AlegraItemStaging.REVIEW_CONFLICT else ("created" if created else "updated")

    @transaction.atomic
    def import_item(self, staging_id, *, category_id=None, calculation_type=None, actor=None):
        """Importa una fila como producto local inactivo, incluso pendiente de clasificación."""
        staging = AlegraItemStaging.objects.select_for_update().get(pk=staging_id)
        if staging.review_status in {AlegraItemStaging.REVIEW_IMPORTED, AlegraItemStaging.REVIEW_IGNORED}:
            raise ValueError("El registro ya fue importado o ignorado.")
        if staging.matched_product_id:
            raise ValueError("Existe una coincidencia local; vincúlala explícitamente en vez de importar.")
        if staging.classification != AlegraItemStaging.CLASS_NEW:
            raise ValueError("El registro no está clasificado como nuevo y requiere revisión antes de importar.")
        existing_map = ExternalObjectMap.objects.filter(
            system=staging.system, resource_type=self.resource_type,
            external_id=staging.external_id,
        ).first()
        if existing_map and existing_map.status == ExternalObjectMap.STATUS_ACTIVE:
            raise ValueError("El ID externo ya tiene un mapeo activo; se bloquea la importación para evitar reasignaciones.")
        from tienda.models import Categoria

        category = Categoria.objects.filter(pk=category_id, activa=True).first() if category_id else None
        if category_id and category is None:
            raise ValueError("Debes seleccionar una categoría local activa.")
        valid_types = {choice[0] for choice in Producto.CALCULO_CHOICES}
        if calculation_type and calculation_type not in valid_types:
            raise ValueError("El tipo de cálculo no es válido.")
        calculation_type = calculation_type or ""
        product = Producto.objects.create(
            nombre=staging.name[:160],
            categoria=category,
            descripcion_corta=staging.description[:240],
            descripcion_larga=staging.description,
            activo=False,
            tipo_calculo=calculation_type,
            precio_base_m2=Decimal("0"),
            precio_base_unidad=Decimal("0"),
            requiere_revision=True,
        )
        from django.contrib.contenttypes.models import ContentType

        ExternalObjectMap.objects.update_or_create(
            system=staging.system,
            resource_type=self.resource_type,
            external_id=staging.external_id,
            defaults={
                "content_type": ContentType.objects.get_for_model(product),
                "object_id": product.pk,
                "status": ExternalObjectMap.STATUS_ACTIVE,
                "last_synced_at": timezone.now(),
                "metadata": {
                    "source": "alegra_item_staging", "staging_id": staging.pk,
                    "external_reference": staging.reference,
                    "external_type": staging.external_type,
                    "reference_price": str(staging.reference_price) if staging.reference_price is not None else None,
                    "external_tax": (staging.technical_data or {}).get("tax"),
                },
            },
        )
        staging.imported_product = product
        staging.review_status = AlegraItemStaging.REVIEW_IMPORTED
        staging.classification = AlegraItemStaging.CLASS_LINKED
        staging.classification_reason = "Producto local creado desde importación aprobada."
        staging.classification_locked = True
        staging.error_detail = ""
        staging.save(update_fields=["imported_product", "review_status", "classification", "classification_reason", "classification_locked", "error_detail", "updated_at"])
        SyncAuditLog.objects.create(
            system=staging.system,
            operation="import_item",
            resource=self.resource_type,
            external_id=staging.external_id,
            actor=actor,
            result=SyncAuditLog.RESULT_SUCCESS,
            detail=f"Producto local {product.pk} creado inactivo.",
            metadata={"staging_id": staging.pk},
        )
        return product

    @transaction.atomic
    def link_item(self, staging_id, product_id, *, actor=None):
        staging = AlegraItemStaging.objects.select_for_update().get(pk=staging_id)
        product = Producto.objects.get(pk=product_id)
        system = staging.system
        from django.contrib.contenttypes.models import ContentType

        existing_map = ExternalObjectMap.objects.filter(system=system, resource_type=self.resource_type, external_id=staging.external_id).first()
        if existing_map and existing_map.object_id and existing_map.object_id != product.pk:
            raise ValueError("El ítem externo ya está vinculado a otro producto local.")

        ExternalObjectMap.objects.update_or_create(
            system=system,
            resource_type=self.resource_type,
            external_id=staging.external_id,
            defaults={
                "content_type": ContentType.objects.get_for_model(product),
                "object_id": product.pk,
                "status": ExternalObjectMap.STATUS_ACTIVE,
                "last_synced_at": timezone.now(),
                "metadata": {"source": "manual_link", "staging_id": staging.pk},
            },
        )
        staging.matched_product = product
        staging.review_status = AlegraItemStaging.REVIEW_MATCH
        staging.classification = AlegraItemStaging.CLASS_LINKED
        staging.classification_reason = "Vinculación manual confirmada."
        staging.classification_locked = True
        staging.imported_product = None
        staging.error_detail = ""
        staging.save(update_fields=["matched_product", "review_status", "classification", "classification_reason", "classification_locked", "imported_product", "error_detail", "updated_at"])
        SyncAuditLog.objects.create(
            system=system,
            operation="link_item",
            resource=self.resource_type,
            external_id=staging.external_id,
            actor=actor,
            result=SyncAuditLog.RESULT_SUCCESS,
            detail=f"Vinculado con producto local {product.pk}.",
            metadata={"staging_id": staging.pk},
        )
        return product

    @transaction.atomic
    def ignore_item(self, staging_id, *, actor=None):
        staging = AlegraItemStaging.objects.select_for_update().get(pk=staging_id)
        staging.review_status = AlegraItemStaging.REVIEW_IGNORED
        staging.classification = AlegraItemStaging.CLASS_IGNORED
        staging.classification_reason = "Excluido manualmente por un administrador."
        staging.classification_locked = True
        staging.save(update_fields=["review_status", "classification", "classification_reason", "classification_locked", "updated_at"])
        SyncAuditLog.objects.create(
            system=staging.system,
            operation="ignore_item",
            resource=self.resource_type,
            external_id=staging.external_id,
            actor=actor,
            result=SyncAuditLog.RESULT_SUCCESS,
            detail="Ítem marcado como ignorado.",
            metadata={"staging_id": staging.pk},
        )

    @transaction.atomic
    def classify_imported_item(self, staging_id, *, category_id=None, calculation_type=None, actor=None):
        """Completa la clasificación local sin generar operaciones hacia Alegra."""
        staging = AlegraItemStaging.objects.select_for_update().select_related("imported_product").get(pk=staging_id)
        product = staging.imported_product
        if product is None:
            raise ValueError("El registro todavía no tiene un producto local importado.")
        from tienda.models import Categoria
        category = Categoria.objects.filter(pk=category_id, activa=True).first() if category_id else None
        if category_id and category is None:
            raise ValueError("La categoría local no existe o está inactiva.")
        valid_types = {choice[0] for choice in Producto.CALCULO_CHOICES}
        if calculation_type and calculation_type not in valid_types:
            raise ValueError("El tipo de cálculo no es válido.")
        product.categoria = category
        product.tipo_calculo = calculation_type or ""
        product.requiere_revision = not bool(category and calculation_type)
        product.save(update_fields=["categoria", "tipo_calculo", "requiere_revision", "actualizado"])
        staging.classification_reason = "Clasificación local actualizada." if not product.requiere_revision else "Producto importado pendiente de configuración."
        staging.save(update_fields=["classification_reason", "updated_at"])
        SyncAuditLog.objects.create(
            system=staging.system, operation="classify_imported_item", resource=self.resource_type,
            external_id=staging.external_id, actor=actor, result=SyncAuditLog.RESULT_SUCCESS,
            detail=f"Producto local {product.pk} clasificado localmente.",
            metadata={"product_id": product.pk, "category_set": bool(category), "calculation_set": bool(calculation_type)},
        )
        return product


class AlegraItemReconciler:
    """Clasifica staging sin crear, activar ni modificar productos comerciales."""

    resource_type = "items"
    UNSUPPORTED_TYPES = {"variant", "variantparent", "kit"}

    def classify(self, *, system=None, staging_ids=None, actor=None):
        qs = AlegraItemStaging.objects.select_related("matched_product", "imported_product")
        if system is not None:
            qs = qs.filter(system=system)
        if staging_ids is not None:
            qs = qs.filter(pk__in=list(staging_ids))
        records = list(qs.order_by("pk"))
        product_index = AlegraItemImporter._product_name_index()
        counts = {key: 0 for key, _ in AlegraItemStaging.CLASSIFICATIONS}
        skipped = 0
        for staging in records:
            if staging.classification_locked:
                skipped += 1
                counts[staging.classification] = counts.get(staging.classification, 0) + 1
                continue
            classification, reason, product = self._classify_one(staging, product_index)
            staging.classification = classification
            staging.classification_reason = reason[:500]
            staging.matched_product = product
            staging.review_status = self._review_status(classification)
            staging.error_detail = reason[:500] if classification == AlegraItemStaging.CLASS_INCOMPLETE else ""
            staging.save(update_fields=["classification", "classification_reason", "matched_product", "review_status", "error_detail", "updated_at"])
            counts[classification] += 1
        if records:
            SyncAuditLog.objects.create(
                system=system or records[0].system,
                operation="classify_items",
                resource=self.resource_type,
                actor=actor,
                result=SyncAuditLog.RESULT_SUCCESS,
                detail=f"Clasificación ejecutada: {len(records)} registros; bloqueados manualmente: {skipped}.",
                metadata={"counts": counts, "manual_locked": skipped},
            )
        return counts

    def _classify_one(self, staging, product_index=None):
        mapped = ExternalObjectMap.objects.filter(
            system=staging.system,
            resource_type=self.resource_type,
            external_id=staging.external_id,
            status=ExternalObjectMap.STATUS_ACTIVE,
        ).first()
        if mapped and isinstance(mapped.local_object, Producto):
            return AlegraItemStaging.CLASS_LINKED, "Existe un mapeo externo válido.", mapped.local_object
        if not normalize_text(staging.name):
            return AlegraItemStaging.CLASS_INCOMPLETE, "Falta el nombre del producto externo.", None
        if normalize_text(staging.external_type) in self.UNSUPPORTED_TYPES:
            return AlegraItemStaging.CLASS_INCOMPLETE, "El tipo externo requiere revisión manual y no se importa automáticamente.", None
        normalized = normalize_text(staging.name)
        candidates = (product_index or AlegraItemImporter._product_name_index()).get(normalized, [])
        if len(candidates) == 1:
            return AlegraItemStaging.CLASS_PROBABLE, "Coincidencia por nombre normalizado; requiere confirmación manual.", candidates[0]
        if len(candidates) > 1:
            return AlegraItemStaging.CLASS_CONFLICT, "Existen múltiples candidatos locales; no se vincula automáticamente.", None
        return AlegraItemStaging.CLASS_NEW, "No se encontró un candidato local.", None

    @staticmethod
    def _review_status(classification):
        return {
            AlegraItemStaging.CLASS_LINKED: AlegraItemStaging.REVIEW_MATCH,
            AlegraItemStaging.CLASS_PROBABLE: AlegraItemStaging.REVIEW_MATCH,
            AlegraItemStaging.CLASS_NEW: AlegraItemStaging.REVIEW_PENDING,
            AlegraItemStaging.CLASS_CONFLICT: AlegraItemStaging.REVIEW_CONFLICT,
            AlegraItemStaging.CLASS_INCOMPLETE: AlegraItemStaging.REVIEW_ERROR,
            AlegraItemStaging.CLASS_IGNORED: AlegraItemStaging.REVIEW_IGNORED,
        }[classification]

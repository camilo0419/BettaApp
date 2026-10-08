# Generated manually because the local execution environment does not contain Django.
# It is equivalent to the migration produced by Django 5.2 for the integration models.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tienda", "0018_clientepuntoventa"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExternalSystem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=50, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("environment", models.CharField(choices=[("production", "Producción"), ("sandbox", "Sandbox")], default="production", max_length=20)),
                ("status", models.CharField(choices=[("active", "Activo"), ("inactive", "Inactivo")], default="active", max_length=20)),
                ("config", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["code"], "verbose_name": "Sistema externo", "verbose_name_plural": "Sistemas externos"},
        ),
        migrations.CreateModel(
            name="AlegraItemStaging",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("external_id", models.CharField(max_length=120)),
                ("name", models.CharField(max_length=150)),
                ("reference", models.CharField(blank=True, max_length=120)),
                ("description", models.TextField(blank=True)),
                ("external_category_id", models.CharField(blank=True, max_length=120)),
                ("external_category_name", models.CharField(blank=True, max_length=150)),
                ("reference_price", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("external_status", models.CharField(blank=True, max_length=30)),
                ("external_type", models.CharField(blank=True, max_length=40)),
                ("review_status", models.CharField(choices=[("pending", "Pendiente"), ("match", "Coincidencia encontrada"), ("conflict", "Conflicto"), ("imported", "Importado"), ("ignored", "Ignorado"), ("error", "Error")], default="pending", max_length=20)),
                ("technical_data", models.JSONField(blank=True, default=dict)),
                ("error_detail", models.CharField(blank=True, max_length=500)),
                ("fetched_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("imported_product", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="alegra_staging_imports", to="tienda.producto")),
                ("matched_product", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="alegra_staging_matches", to="tienda.producto")),
                ("system", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="alegra_items", to="tienda.externalsystem")),
            ],
            options={"ordering": ["-fetched_at", "name"], "verbose_name": "Ítem Alegra en revisión", "verbose_name_plural": "Ítems Alegra en revisión"},
        ),
        migrations.CreateModel(
            name="ExternalObjectMap",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("resource_type", models.CharField(max_length=80)),
                ("external_id", models.CharField(max_length=120)),
                ("object_id", models.PositiveBigIntegerField(blank=True, null=True)),
                ("status", models.CharField(choices=[("active", "Activo"), ("unlinked", "Desvinculado"), ("error", "Error")], default="active", max_length=20)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("content_type", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="contenttypes.contenttype")),
                ("system", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="object_maps", to="tienda.externalsystem")),
            ],
            options={"ordering": ["system", "resource_type", "external_id"], "verbose_name": "Mapeo de objeto externo", "verbose_name_plural": "Mapeos de objetos externos"},
        ),
        migrations.CreateModel(
            name="SyncAuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("operation", models.CharField(max_length=80)),
                ("resource", models.CharField(max_length=80)),
                ("external_id", models.CharField(blank=True, max_length=120)),
                ("result", models.CharField(choices=[("success", "Exitoso"), ("partial", "Parcial"), ("error", "Error")], max_length=20)),
                ("detail", models.CharField(blank=True, max_length=500)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="integration_audit_logs", to=settings.AUTH_USER_MODEL)),
                ("system", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="audit_logs", to="tienda.externalsystem")),
            ],
            options={"ordering": ["-created_at", "-id"], "verbose_name": "Auditoría de sincronización", "verbose_name_plural": "Auditorías de sincronización"},
        ),
        migrations.AddConstraint(
            model_name="alegraitemstaging",
            constraint=models.UniqueConstraint(fields=("system", "external_id"), name="unique_alegra_staging_item"),
        ),
        migrations.AddConstraint(
            model_name="externalobjectmap",
            constraint=models.UniqueConstraint(fields=("system", "resource_type", "external_id"), name="unique_external_object_identity"),
        ),
        migrations.AddIndex(model_name="alegraitemstaging", index=models.Index(fields=["review_status", "fetched_at"], name="alegra_stage_status_idx")),
        migrations.AddIndex(model_name="alegraitemstaging", index=models.Index(fields=["reference"], name="alegra_stage_ref_idx")),
        migrations.AddIndex(model_name="externalobjectmap", index=models.Index(fields=["content_type", "object_id"], name="external_map_local_idx")),
        migrations.AddIndex(model_name="syncauditlog", index=models.Index(fields=["resource", "created_at"], name="sync_audit_resource_idx")),
        migrations.AddIndex(model_name="syncauditlog", index=models.Index(fields=["result", "created_at"], name="sync_audit_result_idx")),
    ]

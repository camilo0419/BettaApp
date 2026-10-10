from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("tienda", "0037_cliente_alegra_sync_policy"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(
        name="AlegraInvoicePreparation",
        fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("mode", models.CharField(choices=[("detailed", "Detallada"), ("consolidated_service", "Consolidada de servicio")], max_length=32)),
            ("status", models.CharField(choices=[("ready", "Lista"), ("blocked", "Bloqueada")], default="blocked", max_length=16)),
            ("payload", models.JSONField(blank=True, default=dict)),
            ("warnings", models.JSONField(blank=True, default=list)),
            ("validation_errors", models.JSONField(blank=True, default=list)),
            ("sale_snapshot_hash", models.CharField(blank=True, max_length=64)),
            ("prepared_at", models.DateTimeField(auto_now=True)),
            ("prepared_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ("venta", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="alegra_preparation", to="tienda.venta")),
        ],
    )]

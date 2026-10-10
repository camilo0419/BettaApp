from django.db import migrations, models


def mark_unlinked_clients_as_historical(apps, schema_editor):
    Cliente = apps.get_model("tienda", "Cliente")
    ExternalObjectMap = apps.get_model("tienda", "ExternalObjectMap")
    alias = schema_editor.connection.alias
    mapped_ids = ExternalObjectMap.objects.using(alias).filter(
        resource_type="contacts",
        status="active",
        object_id__isnull=False,
    ).values_list("object_id", flat=True)
    Cliente.objects.using(alias).exclude(pk__in=mapped_ids).update(
        alegra_sync_policy="historical_pending",
    )


def restore_auto_policy(apps, schema_editor):
    Cliente = apps.get_model("tienda", "Cliente")
    Cliente.objects.using(schema_editor.connection.alias).filter(
        alegra_sync_policy="historical_pending",
    ).update(alegra_sync_policy="auto")


class Migration(migrations.Migration):

    dependencies = [
        ("tienda", "0036_alegraproductwriteoperation"),
    ]

    operations = [
        migrations.AddField(
            model_name="cliente",
            name="alegra_sync_policy",
            field=models.CharField(
                choices=[
                    ("auto", "Sincronización automática"),
                    ("historical_pending", "Pendiente de conciliación histórica"),
                ],
                default="auto",
                max_length=32,
            ),
        ),
        migrations.RunPython(mark_unlinked_clients_as_historical, restore_auto_policy),
    ]

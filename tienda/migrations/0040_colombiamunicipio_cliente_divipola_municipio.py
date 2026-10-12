from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("tienda", "0039_cliente_punto_venta_next_code")]

    operations = [
        migrations.CreateModel(
            name="ColombiaMunicipio",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("codigo", models.CharField(max_length=5)),
                ("codigo_departamento", models.CharField(max_length=2)),
                ("nombre", models.CharField(max_length=120)),
                ("departamento", models.CharField(max_length=120)),
                ("version", models.CharField(max_length=30)),
                ("activo", models.BooleanField(default=True)),
            ],
            options={
                "ordering": ["departamento", "nombre", "codigo"],
                "indexes": [
                    models.Index(fields=["version", "activo", "nombre"], name="colombia_mpio_name_idx"),
                    models.Index(fields=["version", "activo", "departamento"], name="colombia_mpio_dept_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("version", "codigo"), name="colombia_mpio_version_code_uniq"),
                ],
            },
        ),
        migrations.AddField(
            model_name="cliente",
            name="divipola_municipio",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="clientes", to="tienda.colombiamunicipio"),
        ),
    ]

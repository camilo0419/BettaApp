from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tienda", "0030_alegrawriteoperation"),
    ]

    operations = [
        migrations.AddField(
            model_name="cliente",
            name="primer_nombre",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="cliente",
            name="segundo_nombre",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="cliente",
            name="primer_apellido",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="cliente",
            name="segundo_apellido",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="cliente",
            name="regimen_tributario",
            field=models.CharField(blank=True, choices=[("COMMON_REGIME", "Régimen común"), ("SIMPLIFIED_REGIME", "Régimen simplificado"), ("NATIONAL_CONSUMPTION_TAX", "Impuesto nacional al consumo"), ("NOT_REPONSIBLE_FOR_CONSUMPTION", "No responsable de consumo"), ("INC_IVA_RESPONSIBLE", "Responsable de IVA"), ("SPECIAL_REGIME", "Régimen especial")], max_length=40),
        ),
    ]

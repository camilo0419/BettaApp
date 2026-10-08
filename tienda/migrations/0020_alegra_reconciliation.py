# Generated manually for the local Alegra reconciliation extension.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tienda", "0019_alegra_integration")]

    operations = [
        migrations.AddField(
            model_name="alegraitemstaging",
            name="classification",
            field=models.CharField(
                choices=[
                    ("linked", "Vinculado"),
                    ("probable", "Coincidencia probable"),
                    ("new", "Nuevo"),
                    ("conflict", "Conflicto"),
                    ("incomplete", "Incompleto"),
                    ("ignored", "Ignorado"),
                ],
                default="incomplete",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="alegraitemstaging",
            name="classification_reason",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="alegraitemstaging",
            name="classification_locked",
            field=models.BooleanField(default=False),
        ),
    ]

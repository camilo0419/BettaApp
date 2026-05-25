from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tienda', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='cotizacion',
            name='notas_internas',
            field=models.TextField(blank=True, help_text='Notas visibles solo para administración'),
        ),
        migrations.AddField(
            model_name='cotizacion',
            name='actualizado',
            field=models.DateTimeField(auto_now=True),
        ),
    ]

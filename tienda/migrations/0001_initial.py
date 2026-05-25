# Generated manually for MVP
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Producto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=120)),
                ('slug', models.SlugField(blank=True, unique=True)),
                ('categoria', models.CharField(max_length=80)),
                ('descripcion_corta', models.TextField()),
                ('imagen', models.CharField(help_text='Ruta static. Ej: tienda/img/vinilo.svg', max_length=255)),
                ('tipo_calculo', models.CharField(choices=[('area_m2', 'Área en m²'), ('unidad', 'Unidad'), ('estimado', 'Estimado / requiere revisión')], default='area_m2', max_length=20)),
                ('precio_base', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('unidad_precio', models.CharField(default='m²', max_length=80)),
                ('minimo_facturable', models.DecimalField(decimal_places=2, default=1, max_digits=10)),
                ('requiere_revision', models.BooleanField(default=False)),
                ('activo', models.BooleanField(default=True)),
                ('orden', models.PositiveIntegerField(default=0)),
            ],
        ),
        migrations.CreateModel(
            name='Cotizacion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre_cliente', models.CharField(max_length=120)),
                ('celular', models.CharField(max_length=40)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('ancho_cm', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('alto_cm', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('cantidad', models.PositiveIntegerField(default=1)),
                ('texto_personalizado', models.CharField(blank=True, max_length=255)),
                ('color', models.CharField(blank=True, max_length=80)),
                ('opciones_json', models.JSONField(blank=True, default=dict)),
                ('archivo', models.FileField(blank=True, null=True, upload_to='cotizaciones/')),
                ('observaciones', models.TextField(blank=True)),
                ('precio_estimado', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('requiere_revision', models.BooleanField(default=False)),
                ('estado', models.CharField(choices=[('recibida', 'Cotización recibida'), ('revision', 'En revisión de diseño'), ('aprobacion', 'Pendiente de aprobación'), ('produccion', 'En producción'), ('lista', 'Lista para entrega'), ('entregada', 'Entregada'), ('cancelada', 'Cancelada')], default='recibida', max_length=20)),
                ('creado', models.DateTimeField(auto_now_add=True)),
                ('producto', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='tienda.producto')),
            ],
            options={'ordering': ['-creado']},
        ),
        migrations.CreateModel(
            name='OpcionProducto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(choices=[('material', 'Material'), ('acabado', 'Acabado'), ('diseno', 'Diseño'), ('extra', 'Extra'), ('color', 'Color'), ('iluminacion', 'Iluminación')], max_length=30)),
                ('nombre', models.CharField(max_length=120)),
                ('valor_extra', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('multiplica_area', models.BooleanField(default=False)),
                ('activo', models.BooleanField(default=True)),
                ('orden', models.PositiveIntegerField(default=0)),
                ('producto', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='opciones', to='tienda.producto')),
            ],
            options={'ordering': ['tipo', 'orden', 'nombre']},
        ),
    ]

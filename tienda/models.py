from decimal import Decimal
from django.db import models
from django.utils.text import slugify


class Producto(models.Model):
    CALCULO_CHOICES = [
        ('area_m2', 'Área en m²'),
        ('unidad', 'Unidad'),
        ('estimado', 'Estimado / requiere revisión'),
    ]

    nombre = models.CharField(max_length=120)
    slug = models.SlugField(unique=True, blank=True)
    categoria = models.CharField(max_length=80)
    descripcion_corta = models.TextField()
    imagen = models.CharField(max_length=255, help_text='Ruta static. Ej: tienda/img/vinilo.svg')
    tipo_calculo = models.CharField(max_length=20, choices=CALCULO_CHOICES, default='area_m2')
    precio_base = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    unidad_precio = models.CharField(max_length=80, default='m²')
    minimo_facturable = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    requiere_revision = models.BooleanField(default=False)
    activo = models.BooleanField(default=True)
    orden = models.PositiveIntegerField(default=0)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.nombre)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nombre


class OpcionProducto(models.Model):
    TIPO_CHOICES = [
        ('material', 'Material'),
        ('acabado', 'Acabado'),
        ('diseno', 'Diseño'),
        ('extra', 'Extra'),
        ('color', 'Color'),
        ('iluminacion', 'Iluminación'),
    ]

    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name='opciones')
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES)
    nombre = models.CharField(max_length=120)
    valor_extra = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    multiplica_area = models.BooleanField(default=False)
    activo = models.BooleanField(default=True)
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['tipo', 'orden', 'nombre']

    def __str__(self):
        return f'{self.producto.nombre} - {self.tipo}: {self.nombre}'


class Cotizacion(models.Model):
    ESTADOS = [
        ('recibida', 'Cotización recibida'),
        ('revision', 'En revisión de diseño'),
        ('aprobacion', 'Pendiente de aprobación'),
        ('produccion', 'En producción'),
        ('lista', 'Lista para entrega'),
        ('entregada', 'Entregada'),
        ('cancelada', 'Cancelada'),
    ]

    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    nombre_cliente = models.CharField(max_length=120)
    celular = models.CharField(max_length=40)
    email = models.EmailField(blank=True)
    ancho_cm = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    alto_cm = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    cantidad = models.PositiveIntegerField(default=1)
    texto_personalizado = models.CharField(max_length=255, blank=True)
    color = models.CharField(max_length=80, blank=True)
    opciones_json = models.JSONField(default=dict, blank=True)
    archivo = models.FileField(upload_to='cotizaciones/', blank=True, null=True)
    observaciones = models.TextField(blank=True)
    notas_internas = models.TextField(blank=True, help_text='Notas visibles solo para administración')
    precio_estimado = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    requiere_revision = models.BooleanField(default=False)
    estado = models.CharField(max_length=20, choices=ESTADOS, default='recibida')
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-creado']

    def __str__(self):
        return f'Cotización #{self.id or "nueva"} - {self.producto.nombre}'

    @property
    def area_m2(self):
        if not self.ancho_cm or not self.alto_cm:
            return Decimal('0')
        return (self.ancho_cm / Decimal('100')) * (self.alto_cm / Decimal('100'))

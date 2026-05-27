from decimal import Decimal
from django.db import models
from django.urls import reverse
from django.utils.text import slugify


class Producto(models.Model):
    CALCULO_MANUAL = "manual"
    CALCULO_AREA = "area_m2"
    CALCULO_UNIDAD = "unidad"
    CALCULO_CHOICES = [
        (CALCULO_MANUAL, "Manual / requiere revisión"),
        (CALCULO_AREA, "Área m²"),
        (CALCULO_UNIDAD, "Por unidad"),
    ]

    nombre = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True, blank=True)
    categoria = models.CharField(max_length=120, blank=True)
    descripcion_corta = models.CharField(max_length=240, blank=True)
    descripcion_larga = models.TextField(blank=True)
    imagen_principal = models.FileField(upload_to="productos/", blank=True, null=True)
    imagen_estatica = models.CharField(max_length=255, blank=True, help_text="Ruta static opcional. Ej: tienda/img/vinilo.svg")
    activo = models.BooleanField(default=True)
    destacado = models.BooleanField(default=True)
    orden = models.PositiveIntegerField(default=0)
    tipo_calculo = models.CharField(max_length=20, choices=CALCULO_CHOICES, default=CALCULO_AREA)
    precio_base_m2 = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    precio_base_unidad = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    requiere_revision = models.BooleanField(default=False)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["orden", "nombre"]
        verbose_name = "Producto"
        verbose_name_plural = "Productos"

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.nombre) or "producto"
            slug = base
            i = 2
            while Producto.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{i}"
                i += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("producto_detalle", kwargs={"slug": self.slug})

    @property
    def campos_activos(self):
        return self.campos.filter(activo=True).order_by("orden", "id")


class ProductoImagen(models.Model):
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="imagenes")
    imagen = models.FileField(upload_to="productos/galeria/")
    titulo = models.CharField(max_length=140, blank=True)
    orden = models.PositiveIntegerField(default=0)
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ["orden", "id"]
        verbose_name = "Imagen de producto"
        verbose_name_plural = "Imágenes de producto"

    def __str__(self):
        return self.titulo or f"Imagen {self.id} - {self.producto}"


class ProductoCampo(models.Model):
    TIPO_TEXTO = "texto"
    TIPO_TEXTO_LARGO = "texto_largo"
    TIPO_NUMERO = "numero"
    TIPO_ENTERO = "entero"
    TIPO_SELECT = "select"
    TIPO_MULTISELECT = "multiselect"
    TIPO_CHECKBOX = "checkbox"
    TIPO_ARCHIVO = "archivo"
    TIPO_IMAGEN = "imagen"
    TIPO_COLOR = "color"
    TIPO_FECHA = "fecha"

    TIPO_CHOICES = [
        (TIPO_TEXTO, "Texto corto"),
        (TIPO_TEXTO_LARGO, "Texto largo"),
        (TIPO_NUMERO, "Número decimal"),
        (TIPO_ENTERO, "Número entero"),
        (TIPO_SELECT, "Selección única"),
        (TIPO_MULTISELECT, "Selección múltiple"),
        (TIPO_CHECKBOX, "Checkbox / Sí-No"),
        (TIPO_ARCHIVO, "Archivo"),
        (TIPO_IMAGEN, "Imagen de referencia"),
        (TIPO_COLOR, "Color"),
        (TIPO_FECHA, "Fecha"),
    ]

    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="campos")
    etiqueta = models.CharField(max_length=160)
    nombre_interno = models.SlugField(max_length=120, blank=True)
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES, default=TIPO_TEXTO)
    obligatorio = models.BooleanField(default=False)
    orden = models.PositiveIntegerField(default=0)
    ayuda = models.CharField(max_length=255, blank=True)
    placeholder = models.CharField(max_length=160, blank=True)
    activo = models.BooleanField(default=True)
    afecta_area_ancho = models.BooleanField(default=False, help_text="Usar este campo como ancho en cm para calcular m²")
    afecta_area_alto = models.BooleanField(default=False, help_text="Usar este campo como alto en cm para calcular m²")
    es_cantidad = models.BooleanField(default=False, help_text="Usar este campo como cantidad")

    class Meta:
        ordering = ["producto", "orden", "id"]
        verbose_name = "Campo configurable"
        verbose_name_plural = "Campos configurables"

    def __str__(self):
        return f"{self.producto} - {self.etiqueta}"

    def save(self, *args, **kwargs):
        if not self.nombre_interno:
            self.nombre_interno = slugify(self.etiqueta).replace("-", "_")[:120]
        super().save(*args, **kwargs)


class CampoOpcion(models.Model):
    AJUSTE_NINGUNO = "ninguno"
    AJUSTE_FIJO = "fijo"
    AJUSTE_POR_M2 = "por_m2"
    AJUSTE_POR_UNIDAD = "por_unidad"
    AJUSTE_PORCENTAJE = "porcentaje"

    AJUSTE_CHOICES = [
        (AJUSTE_NINGUNO, "No suma precio"),
        (AJUSTE_FIJO, "Valor fijo"),
        (AJUSTE_POR_M2, "Valor por m²"),
        (AJUSTE_POR_UNIDAD, "Valor por unidad"),
        (AJUSTE_PORCENTAJE, "Porcentaje sobre base"),
    ]

    campo = models.ForeignKey(ProductoCampo, on_delete=models.CASCADE, related_name="opciones")
    etiqueta = models.CharField(max_length=160)
    valor = models.SlugField(max_length=120, blank=True)
    ajuste_tipo = models.CharField(max_length=20, choices=AJUSTE_CHOICES, default=AJUSTE_NINGUNO)
    precio = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    orden = models.PositiveIntegerField(default=0)
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ["campo", "orden", "id"]
        verbose_name = "Opción de campo"
        verbose_name_plural = "Opciones de campo"

    def __str__(self):
        return f"{self.campo.etiqueta}: {self.etiqueta}"

    def save(self, *args, **kwargs):
        if not self.valor:
            self.valor = slugify(self.etiqueta)[:120]
        super().save(*args, **kwargs)


class Solicitud(models.Model):
    ESTADO_NUEVA = "nueva"
    ESTADO_REVISION = "revision"
    ESTADO_PENDIENTE_INFO = "pendiente_info"
    ESTADO_COTIZADA = "cotizada"
    ESTADO_APROBADA = "aprobada"
    ESTADO_PRODUCCION = "produccion"
    ESTADO_LISTA = "lista"
    ESTADO_ENTREGADA = "entregada"
    ESTADO_CANCELADA = "cancelada"

    ESTADOS = [
        (ESTADO_NUEVA, "Nueva"),
        (ESTADO_REVISION, "En revisión"),
        (ESTADO_PENDIENTE_INFO, "Pendiente de información"),
        (ESTADO_COTIZADA, "Cotizada"),
        (ESTADO_APROBADA, "Aprobada"),
        (ESTADO_PRODUCCION, "En producción"),
        (ESTADO_LISTA, "Lista para entrega"),
        (ESTADO_ENTREGADA, "Entregada"),
        (ESTADO_CANCELADA, "Cancelada"),
    ]

    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name="solicitudes")
    cliente_nombre = models.CharField(max_length=160)
    cliente_celular = models.CharField(max_length=40)
    cliente_email = models.EmailField(blank=True)
    estado = models.CharField(max_length=30, choices=ESTADOS, default=ESTADO_NUEVA)
    precio_estimado = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    precio_final = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    requiere_revision = models.BooleanField(default=False)
    notas_internas = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-creado"]
        verbose_name = "Solicitud"
        verbose_name_plural = "Solicitudes"

    def __str__(self):
        return f"Solicitud #{self.id} - {self.producto}"


class SolicitudRespuesta(models.Model):
    solicitud = models.ForeignKey(Solicitud, on_delete=models.CASCADE, related_name="respuestas")
    campo = models.ForeignKey(ProductoCampo, on_delete=models.SET_NULL, null=True, blank=True)
    etiqueta = models.CharField(max_length=160)
    tipo = models.CharField(max_length=30)
    valor_texto = models.TextField(blank=True)
    archivo = models.FileField(upload_to="solicitudes/archivos/", blank=True, null=True)
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["orden", "id"]
        verbose_name = "Respuesta de solicitud"
        verbose_name_plural = "Respuestas de solicitud"

    def __str__(self):
        return f"{self.solicitud_id} - {self.etiqueta}"

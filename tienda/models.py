from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.text import slugify


class Categoria(models.Model):
    nombre = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    orden = models.PositiveIntegerField(default=0)
    activa = models.BooleanField(default=True)
    creada = models.DateTimeField(auto_now_add=True)
    actualizada = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["orden", "nombre"]
        verbose_name = "Categoría"
        verbose_name_plural = "Categorías"

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.nombre) or "categoria"
            slug = base
            i = 2
            while Categoria.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{i}"
                i += 1
            self.slug = slug
        super().save(*args, **kwargs)


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
    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT, related_name="productos")
    descripcion_corta = models.CharField(max_length=240, blank=True)
    descripcion_larga = models.TextField(blank=True)
    imagen_principal = models.FileField(upload_to="productos/", blank=True, null=True)
    imagen_estatica = models.CharField(max_length=255, blank=True, help_text="Ruta static opcional. Ej: tienda/img/vinilo.svg")
    activo = models.BooleanField(default=True)
    destacado = models.BooleanField("Destacado en home", default=True)
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
    campo_maestro = models.ForeignKey(
        "CampoMaestro",
        on_delete=models.PROTECT,
        related_name="producto_campos",
        null=True,
        blank=True,
    )
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
        constraints = [
            models.UniqueConstraint(
                fields=["producto"],
                condition=models.Q(afecta_area_ancho=True),
                name="unico_campo_ancho_por_producto",
            ),
            models.UniqueConstraint(
                fields=["producto"],
                condition=models.Q(afecta_area_alto=True),
                name="unico_campo_alto_por_producto",
            ),
            models.UniqueConstraint(
                fields=["producto"],
                condition=models.Q(es_cantidad=True),
                name="unico_campo_cantidad_por_producto",
            ),
            models.UniqueConstraint(
                fields=["producto", "campo_maestro"],
                condition=models.Q(campo_maestro__isnull=False),
                name="unico_campo_maestro_por_producto",
            ),
        ]

    def __str__(self):
        return f"{self.producto} - {self.etiqueta}"

    def clean(self):
        super().clean()
        errors = {}
        roles = [
            ("afecta_area_ancho", "ancho"),
            ("afecta_area_alto", "alto"),
            ("es_cantidad", "cantidad"),
        ]
        roles_marcados = [field for field, _label in roles if getattr(self, field)]

        if len(roles_marcados) > 1:
            mensaje = "Un campo no puede tener mas de un rol de calculo."
            for field in roles_marcados:
                errors[field] = mensaje

        if self.producto_id:
            if self.campo_maestro_id:
                if self.tipo != self.campo_maestro.tipo:
                    errors["tipo"] = "El tipo debe coincidir con el campo maestro asignado."

                campos = ProductoCampo.objects.filter(
                    producto_id=self.producto_id,
                    campo_maestro_id=self.campo_maestro_id,
                )
                if self.pk:
                    campos = campos.exclude(pk=self.pk)
                if campos.exists():
                    errors["campo_maestro"] = "Este campo maestro ya esta asignado a este producto."

            for field, label in roles:
                if getattr(self, field):
                    campos = ProductoCampo.objects.filter(producto_id=self.producto_id, **{field: True})
                    if self.pk:
                        campos = campos.exclude(pk=self.pk)
                    if campos.exists():
                        errors[field] = f"Este producto ya tiene un campo marcado como {label}."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.nombre_interno:
            if self.campo_maestro_id:
                self.nombre_interno = self.campo_maestro.slug.replace("-", "_")[:120]
            else:
                self.nombre_interno = slugify(self.etiqueta).replace("-", "_")[:120]
        self.full_clean()
        super().save(*args, **kwargs)

    @classmethod
    def desde_maestro(cls, producto, campo_maestro):
        return cls(
            producto=producto,
            campo_maestro=campo_maestro,
            etiqueta=campo_maestro.etiqueta_base or campo_maestro.nombre,
            nombre_interno=campo_maestro.slug.replace("-", "_")[:120],
            tipo=campo_maestro.tipo,
            obligatorio=campo_maestro.obligatorio_base,
            ayuda=campo_maestro.ayuda_base,
            placeholder=campo_maestro.placeholder_base,
            orden=campo_maestro.orden_base,
            activo=campo_maestro.activo,
        )

    def copiar_opciones_maestras(self):
        if not self.campo_maestro_id:
            return 0

        creadas = 0
        opciones = self.campo_maestro.opciones_maestras.filter(activa=True).order_by("orden", "id")
        for opcion_maestra in opciones:
            valor = opcion_maestra.valor or slugify(opcion_maestra.etiqueta)[:120]
            _opcion, creada = CampoOpcion.objects.get_or_create(
                campo=self,
                valor=valor,
                defaults={
                    "etiqueta": opcion_maestra.etiqueta,
                    "ajuste_tipo": opcion_maestra.ajuste_tipo,
                    "precio": opcion_maestra.precio,
                    "orden": opcion_maestra.orden,
                    "activa": opcion_maestra.activa,
                },
            )
            if creada:
                creadas += 1
        return creadas


class CampoMaestro(models.Model):
    nombre = models.CharField(max_length=160)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    tipo = models.CharField(max_length=30, choices=ProductoCampo.TIPO_CHOICES, default=ProductoCampo.TIPO_TEXTO)
    etiqueta_base = models.CharField(max_length=160, blank=True)
    ayuda_base = models.CharField(max_length=255, blank=True)
    placeholder_base = models.CharField(max_length=160, blank=True)
    obligatorio_base = models.BooleanField(default=False)
    activo = models.BooleanField(default=True)
    orden_base = models.PositiveIntegerField(default=0)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["orden_base", "nombre"]
        verbose_name = "Campo maestro"
        verbose_name_plural = "Campos maestros"

    def __str__(self):
        return self.nombre

    def clean(self):
        super().clean()
        if self.activo and CampoMaestro.objects.filter(
            activo=True,
            nombre__iexact=self.nombre,
            tipo=self.tipo,
        ).exclude(pk=self.pk).exists():
            raise ValidationError({"nombre": "Ya existe un campo maestro activo con este nombre y tipo."})

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.nombre) or "campo-maestro"
            slug = base
            i = 2
            while CampoMaestro.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{i}"
                i += 1
            self.slug = slug
        if not self.etiqueta_base:
            self.etiqueta_base = self.nombre
        self.full_clean()
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
        constraints = [
            models.UniqueConstraint(fields=["campo", "valor"], name="unica_opcion_por_campo_valor"),
        ]

    def __str__(self):
        return f"{self.campo.etiqueta}: {self.etiqueta}"

    def clean(self):
        super().clean()
        if self.campo_id and self.valor:
            opciones = CampoOpcion.objects.filter(campo_id=self.campo_id, valor=self.valor)
            if self.pk:
                opciones = opciones.exclude(pk=self.pk)
            if opciones.exists():
                raise ValidationError({"valor": "Ya existe una opcion con este valor en este campo."})

    def save(self, *args, **kwargs):
        if not self.valor:
            self.valor = slugify(self.etiqueta)[:120]
        self.full_clean()
        super().save(*args, **kwargs)


class CampoMaestroOpcion(models.Model):
    campo_maestro = models.ForeignKey(CampoMaestro, on_delete=models.CASCADE, related_name="opciones_maestras")
    etiqueta = models.CharField(max_length=160)
    valor = models.SlugField(max_length=120, blank=True)
    ajuste_tipo = models.CharField(max_length=20, choices=CampoOpcion.AJUSTE_CHOICES, default=CampoOpcion.AJUSTE_NINGUNO)
    precio = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    orden = models.PositiveIntegerField(default=0)
    activa = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["campo_maestro", "orden", "id"]
        verbose_name = "Opción maestra"
        verbose_name_plural = "Opciones maestras"
        constraints = [
            models.UniqueConstraint(fields=["campo_maestro", "valor"], name="unica_opcion_maestra_por_valor"),
        ]

    def __str__(self):
        return f"{self.campo_maestro}: {self.etiqueta}"

    def clean(self):
        super().clean()
        if self.campo_maestro_id and self.valor:
            opciones = CampoMaestroOpcion.objects.filter(campo_maestro_id=self.campo_maestro_id, valor=self.valor)
            if self.pk:
                opciones = opciones.exclude(pk=self.pk)
            if opciones.exists():
                raise ValidationError({"valor": "Ya existe una opcion maestra con este valor en este campo."})

    def save(self, *args, **kwargs):
        if not self.valor:
            self.valor = slugify(self.etiqueta)[:120]
        self.full_clean()
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

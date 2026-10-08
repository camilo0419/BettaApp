from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
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


class UNSPSCCode(models.Model):
    LEVEL_SEGMENT = "segment"
    LEVEL_FAMILY = "family"
    LEVEL_CLASS = "class"
    LEVEL_PRODUCT = "product"
    LEVEL_CHOICES = [
        (LEVEL_SEGMENT, "Segmento"),
        (LEVEL_FAMILY, "Familia"),
        (LEVEL_CLASS, "Clase"),
        (LEVEL_PRODUCT, "Producto"),
    ]

    code = models.CharField(max_length=8)
    description = models.CharField(max_length=255)
    level = models.CharField(max_length=12, choices=LEVEL_CHOICES)
    parent = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="children")
    catalog_version = models.CharField(max_length=40)
    active = models.BooleanField(default=True)
    source = models.CharField(max_length=255, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["catalog_version", "code"], name="unspsc_version_code_unique"),
        ]
        indexes = [
            models.Index(fields=["code"], name="unspsc_code_idx"),
            models.Index(fields=["catalog_version", "active"], name="unspsc_version_active_idx"),
            models.Index(fields=["description"], name="unspsc_description_idx"),
        ]
        verbose_name = "Código UNSPSC"
        verbose_name_plural = "Códigos UNSPSC"

    def clean(self):
        super().clean()
        code = str(self.code or "")
        if len(code) != 8 or not code.isdigit():
            raise ValidationError({"code": "El código UNSPSC debe tener exactamente 8 dígitos."})
        expected_level = {"000000": self.LEVEL_SEGMENT, "0000": self.LEVEL_FAMILY, "00": self.LEVEL_CLASS, "": self.LEVEL_PRODUCT}
        suffix = "000000" if code.endswith("000000") else "0000" if code.endswith("0000") else "00" if code.endswith("00") else ""
        if self.level and self.level != expected_level[suffix]:
            raise ValidationError({"level": "El nivel no corresponde a la longitud oficial del código UNSPSC."})

    def __str__(self):
        return f"{self.code} · {self.description}"


class UNSPSCImportJob(models.Model):
    STATUS_PENDING = "pending"
    STATUS_READY = "ready"
    STATUS_APPLY_REQUESTED = "apply_requested"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUSES = [
        (STATUS_PENDING, "Pendiente de validación"),
        (STATUS_READY, "Vista previa lista"),
        (STATUS_APPLY_REQUESTED, "Aplicación solicitada"),
        (STATUS_PROCESSING, "Procesando"),
        (STATUS_COMPLETED, "Completada"),
        (STATUS_FAILED, "Fallida"),
    ]

    file = models.FileField(upload_to="private/unspsc/", max_length=255)
    catalog_version = models.CharField(max_length=40)
    source_url = models.URLField(max_length=500, blank=True)
    status = models.CharField(max_length=24, choices=STATUSES, default=STATUS_PENDING)
    source_rows = models.PositiveIntegerField(default=0)
    unique_codes = models.PositiveIntegerField(default=0)
    invalid_rows = models.PositiveIntegerField(default=0)
    duplicate_rows = models.PositiveIntegerField(default=0)
    preview = models.JSONField(default=dict, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["status", "created_at"], name="unspsc_job_status_idx")]
        verbose_name = "Importación de catálogo UNSPSC"
        verbose_name_plural = "Importaciones de catálogo UNSPSC"

    def __str__(self):
        return f"UNSPSC {self.catalog_version} · {self.get_status_display()}"


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
    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT, related_name="productos", null=True, blank=True)
    unspsc = models.ForeignKey(UNSPSCCode, on_delete=models.PROTECT, related_name="productos", null=True, blank=True)
    descripcion_corta = models.CharField(max_length=240, blank=True)
    descripcion_larga = models.TextField(blank=True)
    imagen_principal = models.ImageField(upload_to="productos/", blank=True, null=True)
    imagen_estatica = models.CharField(max_length=255, blank=True, help_text="Ruta static opcional. Ej: tienda/img/vinilo.svg")
    activo = models.BooleanField(default=True)
    destacado = models.BooleanField("Destacado en home", default=True)
    orden = models.PositiveIntegerField(default=0)
    tipo_calculo = models.CharField(max_length=20, choices=CALCULO_CHOICES, default="", blank=True)
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
    imagen = models.ImageField(upload_to="productos/galeria/")
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
    TIPO_VALOR_FIJO = "valor_fijo"

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
        (TIPO_VALOR_FIJO, "Valor fijo"),
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
    valor_fijo = models.CharField(max_length=255, blank=True)
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

        if self.tipo == self.TIPO_VALOR_FIJO:
            if not self.valor_fijo.strip():
                errors["valor_fijo"] = "El valor fijo es obligatorio para este tipo de campo."
            for field in roles_marcados:
                errors[field] = "Un valor fijo no puede participar en el calculo."

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
            valor_fijo=campo_maestro.valor_fijo_base,
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
    valor_fijo_base = models.CharField(max_length=255, blank=True)
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
        errors = {}
        if self.activo and CampoMaestro.objects.filter(
            activo=True,
            nombre__iexact=self.nombre,
            tipo=self.tipo,
        ).exclude(pk=self.pk).exists():
            errors["nombre"] = "Ya existe un campo maestro activo con este nombre y tipo."
        if self.tipo == ProductoCampo.TIPO_VALOR_FIJO and not self.valor_fijo_base.strip():
            errors["valor_fijo_base"] = "El valor fijo base es obligatorio para este tipo de campo."
        if errors:
            raise ValidationError(errors)

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
                raise ValidationError({"valor": "Ya existe una opción con este valor en este campo."})

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
                raise ValidationError({"valor": "Ya existe una opción maestra con este valor en este campo."})

    def save(self, *args, **kwargs):
        if not self.valor:
            self.valor = slugify(self.etiqueta)[:120]
        self.full_clean()
        super().save(*args, **kwargs)


class Cliente(models.Model):
    TIPO_PERSONA = "persona"
    TIPO_EMPRESA = "empresa"
    TIPO_CHOICES = [
        (TIPO_PERSONA, "Persona"),
        (TIPO_EMPRESA, "Empresa"),
    ]

    ID_CC = "cc"
    ID_NIT = "nit"
    ID_CE = "ce"
    ID_PASAPORTE = "pasaporte"
    ID_OTRO = "otro"
    TIPO_IDENTIFICACION_CHOICES = [
        (ID_CC, "CC"),
        (ID_NIT, "NIT"),
        (ID_CE, "CE"),
        (ID_PASAPORTE, "Pasaporte"),
        (ID_OTRO, "Otro"),
    ]

    ALEGRA_REGIME_CHOICES = [
        ("COMMON_REGIME", "Régimen común"),
        ("SIMPLIFIED_REGIME", "Régimen simplificado"),
        ("NATIONAL_CONSUMPTION_TAX", "Impuesto nacional al consumo"),
        ("NOT_REPONSIBLE_FOR_CONSUMPTION", "No responsable de consumo"),
        ("INC_IVA_RESPONSIBLE", "Responsable de IVA"),
        ("SPECIAL_REGIME", "Régimen especial"),
    ]

    tipo_cliente = models.CharField(max_length=20, choices=TIPO_CHOICES, default=TIPO_PERSONA)
    nombre = models.CharField(max_length=180)
    primer_nombre = models.CharField(max_length=80, blank=True)
    segundo_nombre = models.CharField(max_length=80, blank=True)
    primer_apellido = models.CharField(max_length=80, blank=True)
    segundo_apellido = models.CharField(max_length=80, blank=True)
    razon_social = models.CharField(max_length=180, blank=True)
    identificacion = models.CharField(max_length=60, blank=True)
    tipo_identificacion = models.CharField(max_length=20, choices=TIPO_IDENTIFICACION_CHOICES, blank=True)
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=40, blank=True)
    telefono_secundario = models.CharField(max_length=40, blank=True)
    celular = models.CharField(max_length=40, blank=True)
    whatsapp = models.CharField(max_length=40, blank=True)
    direccion = models.CharField(max_length=255, blank=True)
    ciudad = models.CharField(max_length=120, blank=True)
    departamento = models.CharField(max_length=120, blank=True)
    pais = models.CharField(max_length=80, blank=True)
    codigo_postal = models.CharField(max_length=20, blank=True)
    digito_verificacion = models.CharField(max_length=4, blank=True)
    regimen_tributario = models.CharField(max_length=40, choices=ALEGRA_REGIME_CHOICES, blank=True)
    contacto_principal = models.CharField(max_length=160, blank=True)
    nombre_comercial = models.CharField(max_length=180, blank=True)
    sector = models.CharField(max_length=120, blank=True)
    sitio_web = models.URLField(blank=True)
    preferencia_contacto = models.CharField(max_length=80, blank=True)
    notas = models.TextField(blank=True)
    activo = models.BooleanField(default=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="clientes_creados",
        null=True,
        blank=True,
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nombre", "razon_social"]
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"
        constraints = [
            models.UniqueConstraint(
                fields=["identificacion"],
                condition=~models.Q(identificacion=""),
                name="cliente_identificacion_unica_si_existe",
            ),
        ]

    def save(self, *args, **kwargs):
        """Mantiene el nombre principal coherente sin alterar históricos incompletos."""
        if self.tipo_cliente == self.TIPO_PERSONA:
            partes = [
                self.primer_nombre,
                self.segundo_nombre,
                self.primer_apellido,
                self.segundo_apellido,
            ]
            nombre_completo = " ".join(str(parte).strip() for parte in partes if str(parte or "").strip())
            if nombre_completo:
                self.nombre = nombre_completo
        elif self.tipo_cliente == self.TIPO_EMPRESA and str(self.razon_social or "").strip() and not str(self.nombre or "").strip():
            self.nombre = self.razon_social.strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nombre_mostrado

    @property
    def nombre_mostrado(self):
        """Nombre comercial legible sin reescribir datos históricos."""
        if self.tipo_cliente == self.TIPO_PERSONA:
            partes = [
                self.primer_nombre,
                self.segundo_nombre,
                self.primer_apellido,
                self.segundo_apellido,
            ]
            nombre_completo = " ".join(str(parte).strip() for parte in partes if str(parte or "").strip())
            if nombre_completo:
                return nombre_completo
        return self.razon_social or self.nombre


class ClientePuntoVenta(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="puntos_venta")
    nombre = models.CharField(max_length=160)
    codigo_interno = models.CharField(max_length=60, blank=True)
    direccion = models.CharField(max_length=255, blank=True)
    ciudad = models.CharField(max_length=120, blank=True)
    departamento = models.CharField(max_length=120, blank=True)
    contacto = models.CharField(max_length=160, blank=True)
    telefono = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    observaciones = models.TextField(blank=True)
    activo = models.BooleanField(default=True)
    es_principal = models.BooleanField(default=False)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-activo", "nombre"]
        verbose_name = "Punto de venta"
        verbose_name_plural = "Puntos de venta"
        constraints = [
            models.UniqueConstraint(fields=["cliente", "nombre"], name="unico_punto_venta_por_cliente_nombre"),
            models.UniqueConstraint(
                fields=["cliente"],
                condition=models.Q(es_principal=True, activo=True),
                name="unico_punto_principal_activo_por_cliente",
            ),
        ]

    def __str__(self):
        return f"{self.cliente} - {self.nombre}"

    def clean(self):
        super().clean()
        if self.cliente_id and self.nombre.strip():
            puntos = ClientePuntoVenta.objects.filter(cliente_id=self.cliente_id, nombre__iexact=self.nombre.strip())
            if self.pk:
                puntos = puntos.exclude(pk=self.pk)
            if puntos.exists():
                raise ValidationError({"nombre": "Ya existe un punto de venta con ese nombre para este cliente."})

    def save(self, *args, **kwargs):
        self.nombre = self.nombre.strip()
        self.full_clean()
        super().save(*args, **kwargs)


class ClienteContacto(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="contactos")
    nombre = models.CharField(max_length=160)
    cargo = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=40, blank=True)
    whatsapp = models.CharField(max_length=40, blank=True)
    es_principal = models.BooleanField(default=False)
    activo = models.BooleanField(default=True)
    notas = models.TextField(blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-es_principal", "nombre"]
        verbose_name = "Contacto de cliente"
        verbose_name_plural = "Contactos de cliente"
        constraints = [
            models.UniqueConstraint(
                fields=["cliente"],
                condition=models.Q(es_principal=True, activo=True),
                name="unico_contacto_principal_activo_por_cliente",
            ),
        ]

    def __str__(self):
        return f"{self.cliente} - {self.nombre}"


class ClienteUsuario(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="usuarios_portal")
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cliente_usuario")
    contacto = models.ForeignKey(ClienteContacto, on_delete=models.SET_NULL, related_name="usuarios_portal", null=True, blank=True)
    activo = models.BooleanField(default=True)
    puede_ver_toda_la_cuenta = models.BooleanField(default=False)
    puede_ver_proyectos = models.BooleanField(default=True)
    puede_ver_solicitudes = models.BooleanField(default=True)
    puede_ver_facturacion = models.BooleanField(default=False)
    puede_descargar_archivos = models.BooleanField(default=True)
    recibe_notificaciones = models.BooleanField(default=True)
    fecha_ultimo_acceso = models.DateTimeField(null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["cliente", "user__email", "user__username"]
        verbose_name = "Usuario de portal cliente"
        verbose_name_plural = "Usuarios de portal cliente"

    def __str__(self):
        return f"{self.cliente} - {self.user.email or self.user.username}"

    def clean(self):
        super().clean()
        errors = {}
        if self.user_id:
            if self.user.is_staff:
                errors["user"] = "Un usuario staff no puede ser usuario de portal cliente."
            if hasattr(self.user, "empleado_perfil"):
                errors["user"] = "Un usuario de producción no puede ser usuario de portal cliente."
        if self.contacto_id and self.cliente_id and self.contacto.cliente_id != self.cliente_id:
            errors["contacto"] = "El contacto debe pertenecer al cliente seleccionado."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Proyecto(models.Model):
    ESTADO_BORRADOR = "borrador"
    ESTADO_PENDIENTE = "pendiente"
    ESTADO_PLANEACION = "en_planeacion"
    ESTADO_PRODUCCION = "en_produccion"
    ESTADO_PAUSADO = "pausado"
    ESTADO_TERMINADO = "terminado"
    ESTADO_ENTREGADO = "entregado"
    ESTADO_CANCELADO = "cancelado"

    ESTADOS = [
        (ESTADO_BORRADOR, "Borrador"),
        (ESTADO_PENDIENTE, "Pendiente"),
        (ESTADO_PLANEACION, "En planeación"),
        (ESTADO_PRODUCCION, "En producción"),
        (ESTADO_PAUSADO, "Pausado"),
        (ESTADO_TERMINADO, "Terminado"),
        (ESTADO_ENTREGADO, "Entregado"),
        (ESTADO_CANCELADO, "Cancelado"),
    ]

    PRIORIDAD_BAJA = "baja"
    PRIORIDAD_NORMAL = "normal"
    PRIORIDAD_ALTA = "alta"
    PRIORIDAD_URGENTE = "urgente"

    PRIORIDADES = [
        (PRIORIDAD_BAJA, "Baja"),
        (PRIORIDAD_NORMAL, "Normal"),
        (PRIORIDAD_ALTA, "Alta"),
        (PRIORIDAD_URGENTE, "Urgente"),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="proyectos", null=True, blank=True)
    contacto = models.ForeignKey(ClienteContacto, on_delete=models.SET_NULL, related_name="proyectos", null=True, blank=True)
    puntos_venta = models.ManyToManyField(ClientePuntoVenta, related_name="proyectos", blank=True)
    nombre = models.CharField(max_length=180)
    cliente_nombre = models.CharField(max_length=160, blank=True)
    cliente_contacto = models.CharField(max_length=160, blank=True)
    cliente_telefono = models.CharField(max_length=40, blank=True)
    cliente_email = models.EmailField(blank=True)
    descripcion = models.TextField(blank=True)
    estado = models.CharField(max_length=30, choices=ESTADOS, default=ESTADO_BORRADOR)
    prioridad = models.CharField(max_length=20, choices=PRIORIDADES, default=PRIORIDAD_NORMAL)
    fecha_inicio = models.DateField(null=True, blank=True)
    fecha_compromiso = models.DateField(null=True, blank=True)
    fecha_cierre = models.DateField(null=True, blank=True)
    responsable = models.ForeignKey(
        "EmpleadoPerfil",
        on_delete=models.PROTECT,
        related_name="proyectos_responsable",
        null=True,
        blank=True,
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="proyectos_creados",
        null=True,
        blank=True,
    )
    activo = models.BooleanField(default=True)
    observaciones = models.TextField(blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fecha_creacion", "nombre"]
        verbose_name = "Proyecto"
        verbose_name_plural = "Proyectos"

    def __str__(self):
        return self.nombre

    def clean(self):
        super().clean()
        errors = {}
        if self.contacto_id:
            if not self.cliente_id:
                errors["contacto"] = "Selecciona un cliente para asociar este contacto."
            elif self.contacto.cliente_id != self.cliente_id:
                errors["contacto"] = "El contacto debe pertenecer al cliente seleccionado."
        if self.pk and self.puntos_venta.exists():
            if not self.cliente_id or self.puntos_venta.exclude(cliente_id=self.cliente_id).exists():
                errors["puntos_venta"] = "Todos los puntos de venta deben pertenecer al cliente del proyecto."
        if errors:
            raise ValidationError(errors)

    @property
    def avance_porcentaje(self):
        tareas = SolicitudTarea.objects.filter(
            models.Q(solicitud__proyecto=self) | models.Q(proyecto=self),
            activa=True,
        ).distinct()
        total_tareas = tareas.count()
        if total_tareas:
            terminadas = tareas.filter(estado__in=[SolicitudTarea.ESTADO_TERMINADA, SolicitudTarea.ESTADO_APROBADA]).count()
            return round((terminadas / total_tareas) * 100)

        solicitudes = self.solicitudes.all()
        total_solicitudes = solicitudes.count()
        if not total_solicitudes:
            return 0
        terminadas = solicitudes.filter(
            estado_produccion__in=[
                Solicitud.PROD_TERMINADO,
                Solicitud.PROD_LISTO_ENTREGA,
                Solicitud.PROD_ENTREGADO,
            ]
        ).count()
        return round((terminadas / total_solicitudes) * 100)


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

    PROD_PENDIENTE_ASIGNAR = "pendiente_asignar"
    PROD_ASIGNADO = "asignado"
    PROD_EN_PROCESO = "en_proceso"
    PROD_CON_NOVEDAD = "con_novedad"
    PROD_TERMINADO = "terminado"
    PROD_CALIDAD = "calidad"
    PROD_LISTO_ENTREGA = "listo_entrega"
    PROD_ENTREGADO = "entregado"
    PROD_CANCELADO = "cancelado"

    ESTADOS_PRODUCCION = [
        (PROD_PENDIENTE_ASIGNAR, "Pendiente por asignar"),
        (PROD_ASIGNADO, "Asignado"),
        (PROD_EN_PROCESO, "En proceso"),
        (PROD_CON_NOVEDAD, "Con novedad"),
        (PROD_TERMINADO, "Terminado"),
        (PROD_CALIDAD, "En control de calidad"),
        (PROD_LISTO_ENTREGA, "Listo para entrega"),
        (PROD_ENTREGADO, "Entregado"),
        (PROD_CANCELADO, "Cancelado"),
    ]

    FACT_PENDIENTE = "pendiente"
    FACT_FACTURADO = "facturado"
    FACT_PAGADO = "pagado"
    FACT_ANULADO = "anulado"

    ESTADOS_FACTURACION = [
        (FACT_PENDIENTE, "Pendiente"),
        (FACT_FACTURADO, "Facturado"),
        (FACT_PAGADO, "Pagado"),
        (FACT_ANULADO, "Anulado"),
    ]

    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name="solicitudes")
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="solicitudes", null=True, blank=True)
    contacto = models.ForeignKey(ClienteContacto, on_delete=models.SET_NULL, related_name="solicitudes", null=True, blank=True)
    proyecto = models.ForeignKey(Proyecto, on_delete=models.SET_NULL, related_name="solicitudes", null=True, blank=True)
    punto_venta = models.ForeignKey(ClientePuntoVenta, on_delete=models.SET_NULL, related_name="solicitudes", null=True, blank=True)
    cliente_nombre = models.CharField(max_length=160)
    cliente_celular = models.CharField(max_length=40)
    cliente_email = models.EmailField(blank=True)
    estado = models.CharField(max_length=30, choices=ESTADOS, default=ESTADO_NUEVA)
    estado_produccion = models.CharField(max_length=30, choices=ESTADOS_PRODUCCION, default=PROD_PENDIENTE_ASIGNAR)
    precio_estimado = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    precio_final = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    valor_facturado = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    estado_facturacion = models.CharField(max_length=20, choices=ESTADOS_FACTURACION, default=FACT_PENDIENTE)
    numero_factura = models.CharField(max_length=80, blank=True)
    fecha_factura = models.DateField(null=True, blank=True)
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

    def clean(self):
        super().clean()
        errors = {}
        proyecto_cliente_id = getattr(self.proyecto, "cliente_id", None)
        cliente_id = self.cliente_id or proyecto_cliente_id
        if self.proyecto_id and self.cliente_id and proyecto_cliente_id and proyecto_cliente_id != self.cliente_id:
            errors["proyecto"] = "El proyecto pertenece a otro cliente."
        if self.contacto_id:
            if not cliente_id:
                errors["contacto"] = "Selecciona un cliente o proyecto para asociar este contacto."
            elif self.contacto.cliente_id != cliente_id:
                errors["contacto"] = "El contacto debe pertenecer al cliente de la solicitud."
        if self.punto_venta_id and self.punto_venta.cliente_id != cliente_id:
            errors["punto_venta"] = "El punto de venta debe pertenecer al cliente de la solicitud."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


def money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class Cotizacion(models.Model):
    ESTADO_BORRADOR = "borrador"
    ESTADO_ENVIADA = "enviada"
    ESTADO_VISTA = "vista"
    ESTADO_APROBADA = "aprobada"
    ESTADO_RECHAZADA = "rechazada"
    ESTADO_VENCIDA = "vencida"
    ESTADO_CONVERTIDA = "convertida"
    ESTADO_ANULADA = "anulada"

    ESTADOS = [
        (ESTADO_BORRADOR, "Borrador"),
        (ESTADO_ENVIADA, "Enviada"),
        (ESTADO_VISTA, "Vista"),
        (ESTADO_APROBADA, "Aprobada"),
        (ESTADO_RECHAZADA, "Rechazada"),
        (ESTADO_VENCIDA, "Vencida"),
        (ESTADO_CONVERTIDA, "Convertida"),
        (ESTADO_ANULADA, "Anulada"),
    ]

    MONEDA_COP = "COP"
    MONEDAS = [(MONEDA_COP, "COP")]

    numero = models.CharField(max_length=30, unique=True, blank=True)
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="cotizaciones")
    contacto = models.ForeignKey(ClienteContacto, on_delete=models.SET_NULL, related_name="cotizaciones", null=True, blank=True)
    proyecto = models.ForeignKey(Proyecto, on_delete=models.SET_NULL, related_name="cotizaciones", null=True, blank=True)
    solicitud = models.ForeignKey(Solicitud, on_delete=models.SET_NULL, related_name="cotizaciones", null=True, blank=True)
    punto_venta = models.ForeignKey(ClientePuntoVenta, on_delete=models.SET_NULL, related_name="cotizaciones", null=True, blank=True)
    titulo = models.CharField(max_length=180)
    descripcion = models.TextField(blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_emision = models.DateField(null=True, blank=True)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    estado = models.CharField(max_length=30, choices=ESTADOS, default=ESTADO_BORRADOR)
    moneda = models.CharField(max_length=10, choices=MONEDAS, default=MONEDA_COP)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    descuento_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    impuesto_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    observaciones_cliente = models.TextField(blank=True)
    condiciones_comerciales = models.TextField(blank=True)
    tiempo_entrega = models.CharField(max_length=160, blank=True)
    forma_pago = models.CharField(max_length=160, blank=True)
    garantia = models.CharField(max_length=160, blank=True)
    validez_dias = models.PositiveIntegerField(default=15)
    enviada_a_email = models.EmailField(blank=True)
    fecha_envio = models.DateTimeField(null=True, blank=True)
    creada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="cotizaciones_creadas", null=True, blank=True)
    actualizada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="cotizaciones_actualizadas", null=True, blank=True)
    activa = models.BooleanField(default=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fecha_creacion", "-id"]
        verbose_name = "Cotización"
        verbose_name_plural = "Cotizaciones"

    def __str__(self):
        return f"{self.numero or 'Cotización'} - {self.cliente}"

    def clean(self):
        super().clean()
        errors = {}
        if self.contacto_id and self.cliente_id and self.contacto.cliente_id != self.cliente_id:
            errors["contacto"] = "El contacto debe pertenecer al cliente seleccionado."
        if self.proyecto_id and self.cliente_id and self.proyecto.cliente_id and self.proyecto.cliente_id != self.cliente_id:
            errors["proyecto"] = "El proyecto pertenece a otro cliente."
        if self.solicitud_id and self.cliente_id:
            solicitud_cliente_id = self.solicitud.cliente_id or getattr(self.solicitud.proyecto, "cliente_id", None)
            if solicitud_cliente_id and solicitud_cliente_id != self.cliente_id:
                errors["solicitud"] = "La solicitud pertenece a otro cliente."
        cliente_id = self.cliente_id or getattr(self.proyecto, "cliente_id", None)
        if self.punto_venta_id and self.punto_venta.cliente_id != cliente_id:
            errors["punto_venta"] = "El punto de venta debe pertenecer al cliente de la cotización."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.numero:
            ultimo_id = Cotizacion.objects.order_by("-id").values_list("id", flat=True).first() or 0
            siguiente = ultimo_id + 1
            numero = f"COT-{siguiente:06d}"
            while Cotizacion.objects.filter(numero=numero).exists():
                siguiente += 1
                numero = f"COT-{siguiente:06d}"
            self.numero = numero
        self.full_clean()
        super().save(*args, **kwargs)

    def recalcular_totales(self):
        items = self.items.filter(activo=True)
        subtotal = sum((item.subtotal for item in items), Decimal("0"))
        descuento = sum((item.descuento_calculado for item in items), Decimal("0"))
        impuesto = sum((item.impuesto_calculado for item in items), Decimal("0"))
        total = sum((item.total for item in items), Decimal("0"))
        self.subtotal = money(subtotal)
        self.descuento_total = money(descuento)
        self.impuesto_total = money(impuesto)
        self.total = money(total)
        self.save(update_fields=["subtotal", "descuento_total", "impuesto_total", "total", "fecha_actualizacion"])

    @property
    def visible_para_cliente(self):
        return self.estado in [
            self.ESTADO_ENVIADA,
            self.ESTADO_VISTA,
            self.ESTADO_APROBADA,
            self.ESTADO_RECHAZADA,
            self.ESTADO_VENCIDA,
            self.ESTADO_CONVERTIDA,
        ] and self.activa


class CotizacionItem(models.Model):
    cotizacion = models.ForeignKey(Cotizacion, on_delete=models.CASCADE, related_name="items")
    producto = models.ForeignKey(Producto, on_delete=models.SET_NULL, related_name="cotizacion_items", null=True, blank=True)
    descripcion = models.CharField(max_length=240)
    detalle = models.TextField(blank=True)
    cantidad = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    unidad = models.CharField(max_length=40, default="und")
    valor_unitario = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    descuento_porcentaje = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    descuento_valor = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    impuesto_porcentaje = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    descuento_calculado = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    impuesto_calculado = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    orden = models.PositiveIntegerField(default=0)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["orden", "id"]
        verbose_name = "Ítem de cotización"
        verbose_name_plural = "Ítems de cotización"

    def __str__(self):
        return f"{self.cotizacion.numero} - {self.descripcion}"

    def clean(self):
        super().clean()
        errors = {}
        if self.cantidad <= 0:
            errors["cantidad"] = "La cantidad debe ser mayor a cero."
        if self.valor_unitario < 0:
            errors["valor_unitario"] = "El valor unitario no puede ser negativo."
        if self.descuento_porcentaje < 0 or self.descuento_porcentaje > 100:
            errors["descuento_porcentaje"] = "El descuento porcentual debe estar entre 0 y 100."
        if self.descuento_valor < 0:
            errors["descuento_valor"] = "El descuento en valor no puede ser negativo."
        if self.impuesto_porcentaje < 0 or self.impuesto_porcentaje > 100:
            errors["impuesto_porcentaje"] = "El impuesto debe estar entre 0 y 100."
        if errors:
            raise ValidationError(errors)

    def calcular_totales(self):
        subtotal = money(self.cantidad * self.valor_unitario)
        descuento_porcentaje_valor = money(subtotal * (self.descuento_porcentaje / Decimal("100")))
        descuento = money(descuento_porcentaje_valor + self.descuento_valor)
        if descuento > subtotal:
            descuento = subtotal
        base = money(subtotal - descuento)
        impuesto = money(base * (self.impuesto_porcentaje / Decimal("100")))
        total = money(base + impuesto)
        self.subtotal = subtotal
        self.descuento_calculado = descuento
        self.impuesto_calculado = impuesto
        self.total = total

    def save(self, *args, **kwargs):
        self.full_clean()
        self.calcular_totales()
        super().save(*args, **kwargs)
        self.cotizacion.recalcular_totales()


class Venta(models.Model):
    ESTADO_BORRADOR = "borrador"
    ESTADO_CONFIRMADA = "confirmada"
    ESTADO_PROCESO = "en_proceso"
    ESTADO_COMPLETADA = "completada"
    ESTADO_CANCELADA = "cancelada"
    ESTADOS = [
        (ESTADO_BORRADOR, "Borrador"),
        (ESTADO_CONFIRMADA, "Confirmada"),
        (ESTADO_PROCESO, "En proceso"),
        (ESTADO_COMPLETADA, "Completada"),
        (ESTADO_CANCELADA, "Cancelada"),
    ]

    numero = models.CharField(max_length=30, unique=True, blank=True)
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="ventas")
    punto_venta = models.ForeignKey(ClientePuntoVenta, on_delete=models.SET_NULL, related_name="ventas", null=True, blank=True)
    proyecto = models.ForeignKey(Proyecto, on_delete=models.SET_NULL, related_name="ventas", null=True, blank=True)
    cotizacion = models.ForeignKey(Cotizacion, on_delete=models.SET_NULL, related_name="ventas", null=True, blank=True)
    solicitud = models.ForeignKey(Solicitud, on_delete=models.SET_NULL, related_name="ventas", null=True, blank=True)
    fecha_venta = models.DateField(default=timezone.localdate)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="ventas_responsables", null=True, blank=True)
    estado = models.CharField(max_length=20, choices=ESTADOS, default=ESTADO_BORRADOR)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    descuento_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    impuesto_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    observaciones = models.TextField(blank=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="ventas_creadas", null=True, blank=True)
    actualizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="ventas_actualizadas", null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)
    cancelado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="ventas_canceladas", null=True, blank=True)
    motivo_cancelacion = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-fecha_venta", "-id"]
        verbose_name = "Venta"
        verbose_name_plural = "Ventas"
        indexes = [models.Index(fields=["fecha_venta", "estado"], name="venta_fecha_estado_idx")]

    def __str__(self):
        return f"{self.numero or 'Venta'} - {self.cliente}"

    def clean(self):
        super().clean()
        errors = {}
        if self.punto_venta_id and self.punto_venta.cliente_id != self.cliente_id:
            errors["punto_venta"] = "El punto de venta debe pertenecer al cliente de la venta."
        if self.proyecto_id and self.proyecto.cliente_id != self.cliente_id:
            errors["proyecto"] = "El proyecto debe pertenecer al cliente de la venta."
        if self.cotizacion_id and self.cotizacion.cliente_id != self.cliente_id:
            errors["cotizacion"] = "La cotización debe pertenecer al cliente de la venta."
        if self.solicitud_id:
            solicitud_cliente_id = self.solicitud.cliente_id or getattr(self.solicitud.proyecto, "cliente_id", None)
            if solicitud_cliente_id and solicitud_cliente_id != self.cliente_id:
                errors["solicitud"] = "La solicitud debe pertenecer al cliente de la venta."
        if self.cotizacion_id and self.cotizacion.proyecto_id and self.proyecto_id and self.cotizacion.proyecto_id != self.proyecto_id:
            errors["proyecto"] = "La venta no puede mezclar proyectos diferentes."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.numero:
            ultimo_id = Venta.objects.order_by("-id").values_list("id", flat=True).first() or 0
            self.numero = f"VEN-{ultimo_id + 1:06d}"
        self.full_clean()
        super().save(*args, **kwargs)

    def recalcular_totales(self):
        items = self.items.filter(activo=True)
        self.subtotal = money(sum((item.subtotal for item in items), Decimal("0")))
        self.descuento_total = money(sum((item.descuento for item in items), Decimal("0")))
        self.impuesto_total = money(sum((item.impuesto for item in items), Decimal("0")))
        self.total = money(sum((item.total for item in items), Decimal("0")))
        self.save(update_fields=["subtotal", "descuento_total", "impuesto_total", "total", "actualizado"])


class VentaItem(models.Model):
    venta = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name="items")
    producto = models.ForeignKey(Producto, on_delete=models.SET_NULL, related_name="venta_items", null=True, blank=True)
    descripcion = models.CharField(max_length=240)
    cantidad = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    unidad = models.CharField(max_length=40, default="und")
    precio_unitario = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    descuento = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    impuesto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    orden = models.PositiveIntegerField(default=0)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["orden", "id"]
        verbose_name = "Ítem de venta"
        verbose_name_plural = "Ítems de venta"

    def __str__(self):
        return f"{self.venta.numero} - {self.descripcion}"

    def clean(self):
        super().clean()
        if self.cantidad <= 0:
            raise ValidationError({"cantidad": "La cantidad debe ser mayor a cero."})
        if self.precio_unitario < 0 or self.descuento < 0 or self.impuesto < 0:
            raise ValidationError("Los valores comerciales no pueden ser negativos.")

    def save(self, *args, **kwargs):
        self.full_clean()
        self.subtotal = money(self.cantidad * self.precio_unitario)
        self.descuento = money(min(self.descuento, self.subtotal))
        base = money(self.subtotal - self.descuento)
        self.impuesto = money(self.impuesto)
        self.total = money(base + self.impuesto)
        super().save(*args, **kwargs)
        self.venta.recalcular_totales()


class AlegraInvoiceStaging(models.Model):
    """Factura de venta leída de Alegra, sin autoridad sobre ventas Betta."""

    STATUS_ACTIVE = "active"
    STATUS_OPEN = "open"
    STATUS_CLOSED = "closed"
    STATUS_VOID = "void"
    STATUS_DRAFT = "draft"
    STATUS_UNKNOWN = "unknown"
    STATUSES = [(STATUS_ACTIVE, "Activa"), (STATUS_OPEN, "Abierta"), (STATUS_CLOSED, "Cerrada"), (STATUS_VOID, "Anulada"), (STATUS_DRAFT, "Borrador"), (STATUS_UNKNOWN, "Desconocida")]
    CLASS_UNREVIEWED = "unreviewed"
    CLASS_MATCHED = "matched"
    CLASS_CONFLICT = "conflict"
    CLASS_IGNORED = "ignored"
    CLASSIFICATIONS = [(CLASS_UNREVIEWED, "Sin conciliar"), (CLASS_MATCHED, "Conciliada"), (CLASS_CONFLICT, "Conflicto"), (CLASS_IGNORED, "Ignorada")]

    system = models.ForeignKey("ExternalSystem", on_delete=models.PROTECT, related_name="alegra_invoices")
    external_id = models.CharField(max_length=120)
    number = models.CharField(max_length=120, blank=True)
    prefix = models.CharField(max_length=40, blank=True)
    issue_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    client_external_id = models.CharField(max_length=120, blank=True)
    client_name = models.CharField(max_length=240, blank=True)
    client_identification = models.CharField(max_length=100, blank=True)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    discount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    tax = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=12, blank=True)
    external_status = models.CharField(max_length=30, choices=STATUSES, default=STATUS_UNKNOWN)
    balance = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    payment_status = models.CharField(max_length=40, blank=True)
    reference = models.CharField(max_length=240, blank=True)
    line_items = models.JSONField(default=list, blank=True)
    original_data = models.JSONField(default=dict, blank=True)
    data_hash = models.CharField(max_length=64, blank=True)
    reconciliation_status = models.CharField(max_length=20, choices=CLASSIFICATIONS, default=CLASS_UNREVIEWED)
    matched_client = models.ForeignKey(Cliente, on_delete=models.SET_NULL, related_name="alegra_invoice_staging", null=True, blank=True)
    fetched_at = models.DateTimeField()
    first_synced_at = models.DateTimeField(auto_now_add=True)
    last_synced_at = models.DateTimeField(auto_now=True)
    error_detail = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-issue_date", "-id"]
        verbose_name = "Factura Alegra en staging"
        verbose_name_plural = "Facturas Alegra en staging"
        constraints = [models.UniqueConstraint(fields=["system", "external_id"], name="unique_alegra_invoice_staging")]
        indexes = [models.Index(fields=["number", "issue_date"], name="alegra_inv_num_date_idx"), models.Index(fields=["reconciliation_status"], name="alegra_inv_recon_idx")]

    def __str__(self):
        return self.number or f"Factura Alegra {self.external_id}"


class VentaFacturaAlegra(models.Model):
    venta = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name="facturas_alegra")
    factura = models.ForeignKey(AlegraInvoiceStaging, on_delete=models.PROTECT, related_name="ventas_betta")
    evidencia = models.CharField(max_length=500)
    confirmado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="conciliaciones_venta_factura")
    creado = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        errors = {}
        venta_cliente_id = getattr(self.venta, "cliente_id", None)
        factura_cliente_id = getattr(self.factura, "matched_client_id", None)
        if venta_cliente_id and factura_cliente_id and venta_cliente_id != factura_cliente_id:
            errors["factura"] = "La factura y la venta deben pertenecer al mismo cliente."
        if not self.evidencia.strip():
            errors["evidencia"] = "La vinculación requiere evidencia comercial."
        if not factura_cliente_id and not self.confirmado_por_id:
            errors["confirmado_por"] = "Una factura sin cliente conciliado requiere revisión autorizada."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["venta", "factura"], name="unique_venta_alegra_invoice_link")]


class AlegraPaymentStaging(models.Model):
    """Ingreso consultado de Alegra; nunca representa un pago local registrado."""

    CLASS_UNREVIEWED = "unreviewed"
    CLASS_MATCHED = "matched"
    CLASS_CONFLICT = "conflict"
    CLASS_IGNORED = "ignored"
    CLASSIFICATIONS = [(CLASS_UNREVIEWED, "Sin conciliar"), (CLASS_MATCHED, "Conciliado"), (CLASS_CONFLICT, "Conflicto"), (CLASS_IGNORED, "Ignorado")]

    system = models.ForeignKey("ExternalSystem", on_delete=models.PROTECT, related_name="alegra_payments")
    external_id = models.CharField(max_length=120)
    payment_date = models.DateField(null=True, blank=True)
    client_external_id = models.CharField(max_length=120, blank=True)
    client_name = models.CharField(max_length=240, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=12, blank=True)
    payment_method = models.CharField(max_length=40, blank=True)
    external_status = models.CharField(max_length=40, blank=True)
    invoice_external_ids = models.JSONField(default=list, blank=True)
    reference = models.CharField(max_length=240, blank=True)
    original_data = models.JSONField(default=dict, blank=True)
    data_hash = models.CharField(max_length=64, blank=True)
    reconciliation_status = models.CharField(max_length=20, choices=CLASSIFICATIONS, default=CLASS_UNREVIEWED)
    matched_client = models.ForeignKey(Cliente, on_delete=models.SET_NULL, related_name="alegra_payment_staging", null=True, blank=True)
    fetched_at = models.DateTimeField()
    first_synced_at = models.DateTimeField(auto_now_add=True)
    last_synced_at = models.DateTimeField(auto_now=True)
    error_detail = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-payment_date", "-id"]
        verbose_name = "Pago Alegra en staging"
        verbose_name_plural = "Pagos Alegra en staging"
        constraints = [models.UniqueConstraint(fields=["system", "external_id"], name="unique_alegra_payment_staging")]
        indexes = [models.Index(fields=["payment_date"], name="alegra_pay_date_idx"), models.Index(fields=["reconciliation_status"], name="alegra_pay_recon_idx")]


class CarteraResponsable(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="asignaciones_cartera")
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="asignaciones_cartera")
    activo = models.BooleanField(default=True)
    asignado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="asignaciones_cartera_creadas")
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["cliente"], condition=models.Q(activo=True), name="unique_active_cartera_responsible")]


class CarteraGestion(models.Model):
    TIPOS = [("llamada", "Llamada"), ("correo", "Correo"), ("whatsapp", "WhatsApp"), ("reunion", "Reunión"), ("otro", "Otro")]
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="gestiones_cartera")
    factura = models.ForeignKey(AlegraInvoiceStaging, on_delete=models.SET_NULL, related_name="gestiones_cartera", null=True, blank=True)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="gestiones_cartera")
    fecha_gestion = models.DateTimeField(default=timezone.now)
    tipo = models.CharField(max_length=20, choices=TIPOS)
    resultado = models.CharField(max_length=240)
    observaciones = models.TextField(blank=True)
    proxima_accion = models.CharField(max_length=240, blank=True)
    fecha_seguimiento = models.DateField(null=True, blank=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="gestiones_cartera_creadas")
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_gestion", "-id"]

    def clean(self):
        super().clean()
        if self.factura_id and self.factura.matched_client_id and self.factura.matched_client_id != self.cliente_id:
            raise ValidationError({"factura": "La factura no pertenece al cliente de la gestión."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class CompromisoPago(models.Model):
    PENDIENTE = "pendiente"
    CUMPLIDO = "cumplido"
    INCUMPLIDO = "incumplido"
    CANCELADO = "cancelado"
    ESTADOS = [(PENDIENTE, "Pendiente"), (CUMPLIDO, "Cumplido"), (INCUMPLIDO, "Incumplido"), (CANCELADO, "Cancelado")]
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="compromisos_pago")
    factura = models.ForeignKey(AlegraInvoiceStaging, on_delete=models.SET_NULL, related_name="compromisos_pago", null=True, blank=True)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="compromisos_pago")
    fecha_comprometida = models.DateField()
    valor_comprometido = models.DecimalField(max_digits=14, decimal_places=2)
    estado = models.CharField(max_length=20, choices=ESTADOS, default=PENDIENTE)
    observaciones = models.TextField(blank=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="compromisos_pago_creados")
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        if self.valor_comprometido <= 0:
            raise ValidationError({"valor_comprometido": "El valor comprometido debe ser mayor que cero."})
        if self.factura_id and self.factura.matched_client_id and self.factura.matched_client_id != self.cliente_id:
            raise ValidationError({"factura": "La factura no pertenece al cliente del compromiso."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class SolicitudRespuesta(models.Model):
    solicitud = models.ForeignKey(Solicitud, on_delete=models.CASCADE, related_name="respuestas")
    campo = models.ForeignKey(ProductoCampo, on_delete=models.SET_NULL, null=True, blank=True)
    etiqueta = models.CharField(max_length=160)
    tipo = models.CharField(max_length=30)
    valor_texto = models.TextField(blank=True)
    archivo = models.FileField(upload_to="solicitudes/archivos/", blank=True, null=True)
    visible_para_cliente = models.BooleanField(default=True)
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["orden", "id"]
        verbose_name = "Respuesta de solicitud"
        verbose_name_plural = "Respuestas de solicitud"

    def __str__(self):
        return f"{self.solicitud_id} - {self.etiqueta}"


class EmpleadoPerfil(models.Model):
    AREA_PRODUCCION = "produccion"
    AREA_DISENO = "diseno"
    AREA_CORTE = "corte"
    AREA_IMPRESION = "impresion"
    AREA_CALIDAD = "calidad"
    AREA_DESPACHO = "despacho"
    AREA_ADMIN = "admin"
    AREA_APOYO = "apoyo"

    AREAS = [
        (AREA_PRODUCCION, "Producción"),
        (AREA_DISENO, "Diseño"),
        (AREA_CORTE, "Corte"),
        (AREA_IMPRESION, "Impresión"),
        (AREA_CALIDAD, "Calidad"),
        (AREA_DESPACHO, "Despacho"),
        (AREA_ADMIN, "Admin"),
        (AREA_APOYO, "Apoyo"),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="empleado_perfil")
    telefono = models.CharField(max_length=40, blank=True)
    cargo = models.CharField(max_length=120, blank=True)
    area = models.CharField(max_length=30, choices=AREAS, default=AREA_PRODUCCION)
    activo = models.BooleanField(default=True)
    puede_recibir_pedidos = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__first_name", "user__last_name", "user__username"]
        verbose_name = "Empleado"
        verbose_name_plural = "Empleados"

    def __str__(self):
        nombre = self.user.get_full_name().strip()
        return nombre or self.user.username


class SolicitudAsignacion(models.Model):
    solicitud = models.ForeignKey(Solicitud, on_delete=models.CASCADE, related_name="asignaciones")
    empleado = models.ForeignKey(EmpleadoPerfil, on_delete=models.PROTECT, related_name="asignaciones")
    asignado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="asignaciones_creadas")
    fecha_asignacion = models.DateTimeField(default=timezone.now)
    activa = models.BooleanField(default=True)
    rol_en_trabajo = models.CharField(max_length=120, blank=True)
    observacion = models.CharField(max_length=255, blank=True)
    fecha_desasignacion = models.DateTimeField(null=True, blank=True)
    desasignado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="asignaciones_desasignadas")

    class Meta:
        ordering = ["-activa", "fecha_asignacion"]
        verbose_name = "Asignación de solicitud"
        verbose_name_plural = "Asignaciones de solicitud"
        constraints = [
            models.UniqueConstraint(
                fields=["solicitud", "empleado"],
                condition=models.Q(activa=True),
                name="unica_asignacion_activa_por_empleado",
            ),
        ]

    def __str__(self):
        return f"Solicitud #{self.solicitud_id} - {self.empleado}"


class SolicitudTarea(models.Model):
    ESTADO_PENDIENTE = "pendiente"
    ESTADO_ASIGNADA = "asignada"
    ESTADO_EN_PROCESO = "en_proceso"
    ESTADO_BLOQUEADA = "bloqueada"
    ESTADO_TERMINADA = "terminada"
    ESTADO_APROBADA = "aprobada"
    ESTADO_CANCELADA = "cancelada"

    ESTADOS = [
        (ESTADO_PENDIENTE, "Pendiente"),
        (ESTADO_ASIGNADA, "Asignada"),
        (ESTADO_EN_PROCESO, "En proceso"),
        (ESTADO_BLOQUEADA, "Bloqueada"),
        (ESTADO_TERMINADA, "Terminada"),
        (ESTADO_APROBADA, "Aprobada"),
        (ESTADO_CANCELADA, "Cancelada"),
    ]

    PRIORIDAD_BAJA = "baja"
    PRIORIDAD_NORMAL = "normal"
    PRIORIDAD_ALTA = "alta"
    PRIORIDAD_URGENTE = "urgente"

    PRIORIDADES = [
        (PRIORIDAD_BAJA, "Baja"),
        (PRIORIDAD_NORMAL, "Normal"),
        (PRIORIDAD_ALTA, "Alta"),
        (PRIORIDAD_URGENTE, "Urgente"),
    ]

    AREA_DISENO = "diseno"
    AREA_PREPRENSA = "preprensa"
    AREA_IMPRESION = "impresion"
    AREA_LAMINADO = "laminado"
    AREA_CORTE = "corte"
    AREA_ENSAMBLE = "ensamble"
    AREA_CALIDAD = "calidad"
    AREA_INSTALACION = "instalacion"
    AREA_DESPACHO = "despacho"
    AREA_APOYO = "apoyo"

    AREAS = [
        (AREA_DISENO, "Diseño"),
        (AREA_PREPRENSA, "Preprensa"),
        (AREA_IMPRESION, "Impresión"),
        (AREA_LAMINADO, "Laminado"),
        (AREA_CORTE, "Corte"),
        (AREA_ENSAMBLE, "Ensamble"),
        (AREA_CALIDAD, "Calidad"),
        (AREA_INSTALACION, "Instalación"),
        (AREA_DESPACHO, "Despacho"),
        (AREA_APOYO, "Apoyo"),
    ]

    solicitud = models.ForeignKey(Solicitud, on_delete=models.CASCADE, related_name="tareas", null=True, blank=True)
    proyecto = models.ForeignKey(Proyecto, on_delete=models.SET_NULL, related_name="tareas", null=True, blank=True)
    titulo = models.CharField(max_length=180)
    descripcion = models.TextField(blank=True)
    responsable = models.ForeignKey(
        EmpleadoPerfil,
        on_delete=models.PROTECT,
        related_name="tareas",
        null=True,
        blank=True,
    )
    asignado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tareas_asignadas",
    )
    area = models.CharField(max_length=30, choices=AREAS, default=AREA_APOYO)
    estado = models.CharField(max_length=30, choices=ESTADOS, default=ESTADO_PENDIENTE)
    prioridad = models.CharField(max_length=20, choices=PRIORIDADES, default=PRIORIDAD_NORMAL)
    orden = models.PositiveIntegerField(default=0)
    fecha_inicio = models.DateField(null=True, blank=True)
    fecha_limite = models.DateField(null=True, blank=True)
    fecha_finalizacion = models.DateTimeField(null=True, blank=True)
    finalizada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tareas_finalizadas",
    )
    activa = models.BooleanField(default=True)
    requiere_evidencia = models.BooleanField(default=False)
    evidencia_archivo = models.FileField(upload_to="solicitudes/tareas/evidencias/", blank=True, null=True)
    observaciones = models.TextField(blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["proyecto", "solicitud", "orden", "fecha_limite", "id"]
        verbose_name = "Tarea de producción"
        verbose_name_plural = "Tareas de producción"

    def __str__(self):
        if self.solicitud_id:
            return f"Solicitud #{self.solicitud_id} - {self.titulo}"
        if self.proyecto_id:
            return f"Proyecto #{self.proyecto_id} - {self.titulo}"
        return self.titulo

    def clean(self):
        super().clean()
        errors = {}
        if not self.solicitud_id and not self.proyecto_id:
            errors["proyecto"] = "Selecciona un proyecto cuando la tarea no pertenece a una solicitud."
        if self.solicitud_id and self.proyecto_id and self.solicitud.proyecto_id and self.solicitud.proyecto_id != self.proyecto_id:
            errors["proyecto"] = "El proyecto debe coincidir con el proyecto de la solicitud."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.solicitud_id and not self.proyecto_id:
            self.proyecto = self.solicitud.proyecto
        if self.responsable_id and self.estado == self.ESTADO_PENDIENTE:
            self.estado = self.ESTADO_ASIGNADA
        self.full_clean()
        super().save(*args, **kwargs)


class SolicitudNovedad(models.Model):
    TIPO_COMENTARIO = "comentario"
    TIPO_CAMBIO_ESTADO = "cambio_estado"
    TIPO_ASIGNACION = "asignacion"
    TIPO_DESASIGNACION = "desasignacion"
    TIPO_EVIDENCIA = "evidencia"
    TIPO_SISTEMA = "sistema"
    TIPO_ALERTA = "alerta"
    TIPO_TAREA_CREADA = "tarea_creada"
    TIPO_TAREA_ASIGNADA = "tarea_asignada"
    TIPO_TAREA_ESTADO = "tarea_estado"
    TIPO_TAREA_EVIDENCIA = "tarea_evidencia"
    TIPO_TAREA_COMENTARIO = "tarea_comentario"
    TIPO_TAREA_FINALIZADA = "tarea_finalizada"

    TIPOS = [
        (TIPO_COMENTARIO, "Comentario"),
        (TIPO_CAMBIO_ESTADO, "Cambio de estado"),
        (TIPO_ASIGNACION, "Asignación"),
        (TIPO_DESASIGNACION, "Desasignación"),
        (TIPO_EVIDENCIA, "Evidencia"),
        (TIPO_SISTEMA, "Sistema"),
        (TIPO_ALERTA, "Alerta"),
        (TIPO_TAREA_CREADA, "Tarea creada"),
        (TIPO_TAREA_ASIGNADA, "Tarea asignada"),
        (TIPO_TAREA_ESTADO, "Estado de tarea"),
        (TIPO_TAREA_EVIDENCIA, "Evidencia de tarea"),
        (TIPO_TAREA_COMENTARIO, "Comentario de tarea"),
        (TIPO_TAREA_FINALIZADA, "Tarea finalizada"),
    ]

    solicitud = models.ForeignKey(Solicitud, on_delete=models.CASCADE, related_name="novedades", null=True, blank=True)
    proyecto = models.ForeignKey(Proyecto, on_delete=models.SET_NULL, related_name="novedades", null=True, blank=True)
    tarea = models.ForeignKey(SolicitudTarea, on_delete=models.SET_NULL, related_name="novedades", null=True, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="novedades_solicitud")
    tipo = models.CharField(max_length=30, choices=TIPOS, default=TIPO_COMENTARIO)
    comentario = models.TextField()
    estado_anterior = models.CharField(max_length=30, blank=True)
    estado_nuevo = models.CharField(max_length=30, blank=True)
    archivo_evidencia = models.FileField(upload_to="solicitudes/evidencias/", blank=True, null=True)
    visible_para_admin = models.BooleanField(default=True)
    visible_para_produccion = models.BooleanField(default=True)
    visible_para_cliente = models.BooleanField(default=False)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_creacion", "-id"]
        verbose_name = "Novedad de solicitud"
        verbose_name_plural = "Novedades de solicitud"

    def __str__(self):
        if self.solicitud_id:
            return f"Solicitud #{self.solicitud_id} - {self.get_tipo_display()}"
        if self.proyecto_id:
            return f"Proyecto #{self.proyecto_id} - {self.get_tipo_display()}"
        return self.get_tipo_display()


class Notificacion(models.Model):
    TIPO_ASIGNACION = "asignacion"
    TIPO_DESASIGNACION = "desasignacion"
    TIPO_ESTADO = "estado"
    TIPO_NOVEDAD = "novedad"
    TIPO_TERMINADO = "terminado"
    TIPO_TAREA = "tarea"
    TIPO_PROYECTO = "proyecto"
    TIPO_SISTEMA = "sistema"

    TIPOS = [
        (TIPO_ASIGNACION, "Asignación"),
        (TIPO_DESASIGNACION, "Desasignación"),
        (TIPO_ESTADO, "Cambio de estado"),
        (TIPO_NOVEDAD, "Novedad"),
        (TIPO_TERMINADO, "Terminado"),
        (TIPO_TAREA, "Tarea"),
        (TIPO_PROYECTO, "Proyecto"),
        (TIPO_SISTEMA, "Sistema"),
    ]

    usuario_destino = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notificaciones")
    solicitud = models.ForeignKey(Solicitud, on_delete=models.CASCADE, null=True, blank=True, related_name="notificaciones")
    tarea = models.ForeignKey(SolicitudTarea, on_delete=models.SET_NULL, null=True, blank=True, related_name="notificaciones")
    proyecto = models.ForeignKey(Proyecto, on_delete=models.SET_NULL, null=True, blank=True, related_name="notificaciones")
    titulo = models.CharField(max_length=160)
    mensaje = models.CharField(max_length=255)
    tipo = models.CharField(max_length=30, choices=TIPOS, default=TIPO_SISTEMA)
    ESTADO_ABIERTA = "abierta"
    ESTADO_RESUELTA = "resuelta"
    ESTADOS = [(ESTADO_ABIERTA, "Abierta"), (ESTADO_RESUELTA, "Resuelta")]
    event_key = models.CharField(max_length=180, null=True, blank=True, default=None)
    estado = models.CharField(max_length=20, choices=ESTADOS, default=ESTADO_ABIERTA)
    resuelta_at = models.DateTimeField(null=True, blank=True)
    leida = models.BooleanField(default=False)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    url_destino = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-fecha_creacion", "-id"]
        verbose_name = "Notificación"
        verbose_name_plural = "Notificaciones"
        constraints = [
            models.UniqueConstraint(fields=["usuario_destino", "event_key"], name="unique_notification_event_per_user"),
        ]

    def __str__(self):
        return f"{self.usuario_destino} - {self.titulo}"


class NotificacionCliente(models.Model):
    TIPO_SISTEMA = "sistema"
    TIPO_PEDIDO = "pedido"
    TIPO_PROYECTO = "proyecto"
    TIPO_FACTURACION = "facturacion"
    TIPO_DOCUMENTO = "documento"
    TIPO_NOVEDAD = "novedad"

    TIPOS = [
        (TIPO_SISTEMA, "Sistema"),
        (TIPO_PEDIDO, "Pedido"),
        (TIPO_PROYECTO, "Proyecto"),
        (TIPO_FACTURACION, "Facturación"),
        (TIPO_DOCUMENTO, "Documento"),
        (TIPO_NOVEDAD, "Novedad"),
    ]

    cliente_usuario = models.ForeignKey(ClienteUsuario, on_delete=models.CASCADE, related_name="notificaciones")
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="notificaciones_cliente")
    solicitud = models.ForeignKey(Solicitud, on_delete=models.CASCADE, null=True, blank=True, related_name="notificaciones_cliente")
    proyecto = models.ForeignKey(Proyecto, on_delete=models.SET_NULL, null=True, blank=True, related_name="notificaciones_cliente")
    titulo = models.CharField(max_length=160)
    mensaje = models.CharField(max_length=255)
    tipo = models.CharField(max_length=30, choices=TIPOS, default=TIPO_SISTEMA)
    leida = models.BooleanField(default=False)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    url_destino = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-fecha_creacion", "-id"]
        verbose_name = "Notificación de cliente"
        verbose_name_plural = "Notificaciones de cliente"

    def __str__(self):
        return f"{self.cliente_usuario} - {self.titulo}"


class ExternalSystem(models.Model):
    """Proveedor externo desacoplado de los modelos comerciales."""

    ENVIRONMENT_PRODUCTION = "production"
    ENVIRONMENT_SANDBOX = "sandbox"
    ENVIRONMENTS = [
        (ENVIRONMENT_PRODUCTION, "Producción"),
        (ENVIRONMENT_SANDBOX, "Sandbox"),
    ]
    STATUS_ACTIVE = "active"
    STATUS_INACTIVE = "inactive"
    STATUSES = [(STATUS_ACTIVE, "Activo"), (STATUS_INACTIVE, "Inactivo")]

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=120)
    environment = models.CharField(max_length=20, choices=ENVIRONMENTS, default=ENVIRONMENT_PRODUCTION)
    status = models.CharField(max_length=20, choices=STATUSES, default=STATUS_ACTIVE)
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Sistema externo"
        verbose_name_plural = "Sistemas externos"

    def __str__(self):
        return f"{self.name} ({self.environment})"


class ExternalObjectMap(models.Model):
    """Identidad estable entre un recurso externo y un objeto local."""

    STATUS_ACTIVE = "active"
    STATUS_UNLINKED = "unlinked"
    STATUS_ERROR = "error"
    STATUSES = [
        (STATUS_ACTIVE, "Activo"),
        (STATUS_UNLINKED, "Desvinculado"),
        (STATUS_ERROR, "Error"),
    ]

    system = models.ForeignKey(ExternalSystem, on_delete=models.PROTECT, related_name="object_maps")
    resource_type = models.CharField(max_length=80)
    external_id = models.CharField(max_length=120)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, null=True, blank=True)
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    local_object = GenericForeignKey("content_type", "object_id")
    status = models.CharField(max_length=20, choices=STATUSES, default=STATUS_ACTIVE)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["system", "resource_type", "external_id"]
        verbose_name = "Mapeo de objeto externo"
        verbose_name_plural = "Mapeos de objetos externos"
        constraints = [
            models.UniqueConstraint(
                fields=["system", "resource_type", "external_id"],
                name="unique_external_object_identity",
            ),
            models.UniqueConstraint(
                fields=["system", "resource_type", "content_type", "object_id"],
                condition=models.Q(status="active", content_type__isnull=False, object_id__isnull=False),
                name="unique_active_external_local_object",
            ),
        ]
        indexes = [
            models.Index(fields=["content_type", "object_id"], name="external_map_local_idx"),
        ]

    def __str__(self):
        return f"{self.system.code}:{self.resource_type}:{self.external_id}"

    def clean(self):
        super().clean()
        if bool(self.content_type_id) != bool(self.object_id):
            raise ValidationError("content_type y object_id deben informarse juntos.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class AlegraItemStaging(models.Model):
    """Ítem leído de Alegra pendiente de revisión local."""

    REVIEW_PENDING = "pending"
    REVIEW_MATCH = "match"
    REVIEW_CONFLICT = "conflict"
    REVIEW_IMPORTED = "imported"
    REVIEW_IGNORED = "ignored"
    REVIEW_ERROR = "error"
    REVIEW_STATUSES = [
        (REVIEW_PENDING, "Pendiente"),
        (REVIEW_MATCH, "Coincidencia encontrada"),
        (REVIEW_CONFLICT, "Conflicto"),
        (REVIEW_IMPORTED, "Importado"),
        (REVIEW_IGNORED, "Ignorado"),
        (REVIEW_ERROR, "Error"),
    ]

    CLASS_LINKED = "linked"
    CLASS_PROBABLE = "probable"
    CLASS_NEW = "new"
    CLASS_CONFLICT = "conflict"
    CLASS_INCOMPLETE = "incomplete"
    CLASS_IGNORED = "ignored"
    CLASSIFICATIONS = [
        (CLASS_LINKED, "Vinculado"),
        (CLASS_PROBABLE, "Coincidencia probable"),
        (CLASS_NEW, "Nuevo"),
        (CLASS_CONFLICT, "Conflicto"),
        (CLASS_INCOMPLETE, "Incompleto"),
        (CLASS_IGNORED, "Ignorado"),
    ]

    system = models.ForeignKey(ExternalSystem, on_delete=models.PROTECT, related_name="alegra_items")
    external_id = models.CharField(max_length=120)
    name = models.CharField(max_length=150)
    reference = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    external_category_id = models.CharField(max_length=120, blank=True)
    external_category_name = models.CharField(max_length=150, blank=True)
    reference_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    external_status = models.CharField(max_length=30, blank=True)
    external_type = models.CharField(max_length=40, blank=True)
    review_status = models.CharField(max_length=20, choices=REVIEW_STATUSES, default=REVIEW_PENDING)
    classification = models.CharField(max_length=20, choices=CLASSIFICATIONS, default=CLASS_INCOMPLETE)
    classification_reason = models.CharField(max_length=500, blank=True)
    classification_locked = models.BooleanField(default=False)
    matched_product = models.ForeignKey(
        Producto,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alegra_staging_matches",
    )
    imported_product = models.ForeignKey(
        Producto,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alegra_staging_imports",
    )
    technical_data = models.JSONField(default=dict, blank=True)
    error_detail = models.CharField(max_length=500, blank=True)
    fetched_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fetched_at", "name"]
        verbose_name = "Ítem Alegra en revisión"
        verbose_name_plural = "Ítems Alegra en revisión"
        constraints = [
            models.UniqueConstraint(
                fields=["system", "external_id"],
                name="unique_alegra_staging_item",
            ),
        ]
        indexes = [
            models.Index(fields=["review_status", "fetched_at"], name="alegra_stage_status_idx"),
            models.Index(fields=["reference"], name="alegra_stage_ref_idx"),
        ]

    def __str__(self):
        return f"{self.name} ({self.external_id})"


class AlegraContactStaging(models.Model):
    """Contacto Alegra depurado, pendiente de conciliación local."""

    CLASS_LINKED = "linked"
    CLASS_PROBABLE = "probable"
    CLASS_NEW = "new"
    CLASS_CONFLICT = "conflict"
    CLASS_INCOMPLETE = "incomplete"
    CLASS_IGNORED = "ignored"
    CLASSIFICATIONS = [
        (CLASS_LINKED, "Vinculado"),
        (CLASS_PROBABLE, "Coincidencia probable"),
        (CLASS_NEW, "Nuevo"),
        (CLASS_CONFLICT, "Conflicto"),
        (CLASS_INCOMPLETE, "Incompleto"),
        (CLASS_IGNORED, "Ignorado"),
    ]

    system = models.ForeignKey(ExternalSystem, on_delete=models.PROTECT, related_name="alegra_contacts")
    external_id = models.CharField(max_length=120)
    external_uuid = models.CharField(max_length=160, blank=True)
    name = models.CharField(max_length=180)
    identification = models.CharField(max_length=80, blank=True)
    identification_type = models.CharField(max_length=40, blank=True)
    verification_digit = models.CharField(max_length=4, blank=True)
    email = models.EmailField(blank=True)
    phone_primary = models.CharField(max_length=40, blank=True)
    phone_secondary = models.CharField(max_length=40, blank=True)
    mobile = models.CharField(max_length=40, blank=True)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=120, blank=True)
    department = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=80, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    external_status = models.CharField(max_length=30, blank=True)
    external_types = models.JSONField(default=list, blank=True)
    classification = models.CharField(max_length=20, choices=CLASSIFICATIONS, default=CLASS_INCOMPLETE)
    classification_reason = models.CharField(max_length=500, blank=True)
    classification_locked = models.BooleanField(default=False)
    matched_client = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True, related_name="alegra_staging_matches")
    imported_client = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True, related_name="alegra_staging_imports")
    technical_data = models.JSONField(default=dict, blank=True)
    error_detail = models.CharField(max_length=500, blank=True)
    fetched_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fetched_at", "name"]
        verbose_name = "Contacto Alegra en revisión"
        verbose_name_plural = "Contactos Alegra en revisión"
        constraints = [models.UniqueConstraint(fields=["system", "external_id"], name="unique_alegra_staging_contact")]
        indexes = [
            models.Index(fields=["classification", "fetched_at"], name="alegra_contact_class_idx"),
            models.Index(fields=["identification"], name="alegra_contact_id_idx"),
        ]

    def __str__(self):
        return f"{self.name} ({self.external_id})"


class SyncAuditLog(models.Model):
    """Auditoría técnica sin secretos ni payloads sensibles completos."""

    RESULT_SUCCESS = "success"
    RESULT_PARTIAL = "partial"
    RESULT_ERROR = "error"
    RESULTS = [
        (RESULT_SUCCESS, "Exitoso"),
        (RESULT_PARTIAL, "Parcial"),
        (RESULT_ERROR, "Error"),
    ]

    system = models.ForeignKey(ExternalSystem, on_delete=models.PROTECT, related_name="audit_logs", null=True, blank=True)
    operation = models.CharField(max_length=80)
    resource = models.CharField(max_length=80)
    external_id = models.CharField(max_length=120, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="integration_audit_logs")
    result = models.CharField(max_length=20, choices=RESULTS)
    detail = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Auditoría de sincronización"
        verbose_name_plural = "Auditorías de sincronización"
        indexes = [
            models.Index(fields=["resource", "created_at"], name="sync_audit_resource_idx"),
            models.Index(fields=["result", "created_at"], name="sync_audit_result_idx"),
        ]

    def __str__(self):
        return f"{self.operation} {self.resource} - {self.result}"


class AlegraWriteOperation(models.Model):
    """Estado durable de una operación externa, sin almacenar secretos ni payloads completos."""

    OP_CREATE = "create"
    OP_UPDATE = "update"
    OPERATIONS = [(OP_CREATE, "Crear contacto"), (OP_UPDATE, "Actualizar contacto")]

    STATE_PENDING = "pending"
    STATE_SENT = "sent"
    STATE_SYNCED = "synced"
    STATE_FAILED = "failed"
    STATE_CONFLICT = "conflict"
    STATE_NEEDS_RECONCILIATION = "needs_reconciliation"
    STATES = [
        (STATE_PENDING, "Pendiente"),
        (STATE_SENT, "Enviada"),
        (STATE_SYNCED, "Sincronizada"),
        (STATE_FAILED, "Fallida"),
        (STATE_CONFLICT, "Conflicto"),
        (STATE_NEEDS_RECONCILIATION, "Requiere conciliación"),
    ]

    system = models.ForeignKey(ExternalSystem, on_delete=models.PROTECT, related_name="write_operations")
    client = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="alegra_write_operations")
    resource_type = models.CharField(max_length=80, default="contacts")
    operation = models.CharField(max_length=20, choices=OPERATIONS)
    external_id = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=32, choices=STATES, default=STATE_PENDING)
    idempotency_key = models.CharField(max_length=64, unique=True)
    attempts = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=40, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    reconciliation_reason = models.CharField(max_length=500, blank=True)
    result_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["system", "client", "state"], name="alegra_write_client_idx"),
            models.Index(fields=["resource_type", "external_id"], name="alegra_write_ext_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(operation__in=["create", "update"]),
                name="alegra_write_operation_valid",
            ),
        ]

    def __str__(self):
        return f"{self.operation}:{self.client_id}:{self.state}"

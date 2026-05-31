from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
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

    tipo_cliente = models.CharField(max_length=20, choices=TIPO_CHOICES, default=TIPO_PERSONA)
    nombre = models.CharField(max_length=180)
    razon_social = models.CharField(max_length=180, blank=True)
    identificacion = models.CharField(max_length=60, blank=True)
    tipo_identificacion = models.CharField(max_length=20, choices=TIPO_IDENTIFICACION_CHOICES, blank=True)
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=40, blank=True)
    whatsapp = models.CharField(max_length=40, blank=True)
    direccion = models.CharField(max_length=255, blank=True)
    ciudad = models.CharField(max_length=120, blank=True)
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
                fields=["email"],
                condition=~models.Q(email=""),
                name="cliente_email_unico_si_existe",
            ),
            models.UniqueConstraint(
                fields=["identificacion"],
                condition=~models.Q(identificacion=""),
                name="cliente_identificacion_unica_si_existe",
            ),
        ]

    def __str__(self):
        return self.razon_social or self.nombre


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
                errors["user"] = "Un usuario de produccion no puede ser usuario de portal cliente."
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
        (ESTADO_PLANEACION, "En planeacion"),
        (ESTADO_PRODUCCION, "En produccion"),
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

    @property
    def avance_porcentaje(self):
        tareas = SolicitudTarea.objects.filter(solicitud__proyecto=self, activa=True)
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
    proyecto = models.ForeignKey(Proyecto, on_delete=models.SET_NULL, related_name="solicitudes", null=True, blank=True)
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
        verbose_name = "Cotizacion"
        verbose_name_plural = "Cotizaciones"

    def __str__(self):
        return f"{self.numero or 'Cotizacion'} - {self.cliente}"

    def save(self, *args, **kwargs):
        if not self.numero:
            ultimo_id = Cotizacion.objects.order_by("-id").values_list("id", flat=True).first() or 0
            siguiente = ultimo_id + 1
            numero = f"COT-{siguiente:06d}"
            while Cotizacion.objects.filter(numero=numero).exists():
                siguiente += 1
                numero = f"COT-{siguiente:06d}"
            self.numero = numero
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
        verbose_name = "Item de cotizacion"
        verbose_name_plural = "Items de cotizacion"

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
        (AREA_PRODUCCION, "Produccion"),
        (AREA_DISENO, "Diseno"),
        (AREA_CORTE, "Corte"),
        (AREA_IMPRESION, "Impresion"),
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
        verbose_name = "Asignacion de solicitud"
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
        (AREA_DISENO, "Diseno"),
        (AREA_PREPRENSA, "Preprensa"),
        (AREA_IMPRESION, "Impresion"),
        (AREA_LAMINADO, "Laminado"),
        (AREA_CORTE, "Corte"),
        (AREA_ENSAMBLE, "Ensamble"),
        (AREA_CALIDAD, "Calidad"),
        (AREA_INSTALACION, "Instalacion"),
        (AREA_DESPACHO, "Despacho"),
        (AREA_APOYO, "Apoyo"),
    ]

    solicitud = models.ForeignKey(Solicitud, on_delete=models.CASCADE, related_name="tareas")
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
        ordering = ["solicitud", "orden", "fecha_limite", "id"]
        verbose_name = "Tarea de produccion"
        verbose_name_plural = "Tareas de produccion"

    def __str__(self):
        return f"Solicitud #{self.solicitud_id} - {self.titulo}"

    def save(self, *args, **kwargs):
        if self.responsable_id and self.estado == self.ESTADO_PENDIENTE:
            self.estado = self.ESTADO_ASIGNADA
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
        (TIPO_ASIGNACION, "Asignacion"),
        (TIPO_DESASIGNACION, "Desasignacion"),
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

    solicitud = models.ForeignKey(Solicitud, on_delete=models.CASCADE, related_name="novedades")
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
        return f"Solicitud #{self.solicitud_id} - {self.get_tipo_display()}"


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
        (TIPO_ASIGNACION, "Asignacion"),
        (TIPO_DESASIGNACION, "Desasignacion"),
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
    leida = models.BooleanField(default=False)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    url_destino = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-fecha_creacion", "-id"]
        verbose_name = "Notificacion"
        verbose_name_plural = "Notificaciones"

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
        (TIPO_FACTURACION, "Facturacion"),
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
        verbose_name = "Notificacion de cliente"
        verbose_name_plural = "Notificaciones de cliente"

    def __str__(self):
        return f"{self.cliente_usuario} - {self.titulo}"

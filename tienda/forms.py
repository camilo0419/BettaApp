from decimal import Decimal, InvalidOperation
from pathlib import Path
from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import models
from .models import (
    CampoMaestro,
    CampoMaestroOpcion,
    CampoOpcion,
    Categoria,
    Cliente,
    ClienteContacto,
    EmpleadoPerfil,
    Producto,
    ProductoCampo,
    ProductoImagen,
    Proyecto,
    Solicitud,
    SolicitudAsignacion,
    SolicitudNovedad,
    SolicitudTarea,
)

User = get_user_model()


BLOCKED_UPLOAD_EXTENSIONS = {
    ".bat", ".cmd", ".com", ".dll", ".exe", ".hta", ".html", ".htm",
    ".js", ".msi", ".php", ".ps1", ".py", ".sh", ".svg", ".vbs",
}
BLOCKED_CONTENT_TYPES = {
    "application/javascript",
    "application/x-msdownload",
    "application/x-msdos-program",
    "application/x-php",
    "image/svg+xml",
    "text/html",
    "text/javascript",
}


def uploaded_file_header(uploaded_file, size=16):
    try:
        position = uploaded_file.tell() if hasattr(uploaded_file, "tell") else None
    except OSError:
        position = None
    header = uploaded_file.read(size)
    if position is not None and hasattr(uploaded_file, "seek"):
        uploaded_file.seek(position)
    return header


def validate_upload(file_obj, allowed_extensions, max_size, image_only=False):
    if not file_obj:
        return file_obj

    extension = Path(file_obj.name).suffix.lower()
    allowed = {item.lower() if item.startswith(".") else f".{item.lower()}" for item in allowed_extensions}
    content_type = (getattr(file_obj, "content_type", "") or "").lower()

    if extension in BLOCKED_UPLOAD_EXTENSIONS or content_type in BLOCKED_CONTENT_TYPES:
        raise ValidationError("Este tipo de archivo no esta permitido.")

    if extension not in allowed:
        raise ValidationError("Extension de archivo no permitida.")

    if file_obj.size > max_size:
        raise ValidationError("El archivo supera el tamano maximo permitido.")

    header = uploaded_file_header(file_obj, 16)
    if image_only or extension in {".jpg", ".jpeg", ".png", ".webp"}:
        is_jpeg = header.startswith(b"\xff\xd8\xff")
        is_png = header.startswith(b"\x89PNG\r\n\x1a\n")
        is_webp = header.startswith(b"RIFF") and header[8:12] == b"WEBP"
        if not (is_jpeg or is_png or is_webp):
            raise ValidationError("El contenido del archivo no coincide con una imagen permitida.")
    elif extension == ".pdf" and not header.startswith(b"%PDF"):
        raise ValidationError("El contenido del archivo no coincide con un PDF valido.")
    elif extension == ".zip" and not header.startswith(b"PK"):
        raise ValidationError("El contenido del archivo no coincide con un ZIP valido.")

    return file_obj


def validate_image_upload(file_obj):
    return validate_upload(
        file_obj,
        settings.ALLOWED_IMAGE_UPLOAD_EXTENSIONS,
        settings.PRODUCT_IMAGE_UPLOAD_MAX_SIZE,
        image_only=True,
    )


def validate_user_upload(file_obj):
    return validate_upload(
        file_obj,
        settings.ALLOWED_USER_UPLOAD_EXTENSIONS,
        settings.USER_UPLOAD_MAX_SIZE,
    )


class ProductoForm(forms.ModelForm):
    class Meta:
        model = Producto
        fields = [
            "nombre", "slug", "categoria", "descripcion_corta", "descripcion_larga",
            "imagen_principal", "imagen_estatica", "activo", "destacado", "orden",
            "tipo_calculo", "precio_base_m2", "precio_base_unidad", "requiere_revision",
        ]
        widgets = {
            "descripcion_larga": forms.Textarea(attrs={"rows": 4}),
        }

    def clean_imagen_principal(self):
        return validate_image_upload(self.cleaned_data.get("imagen_principal"))


class CategoriaForm(forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ["nombre", "slug", "orden", "activa"]


class ProductoImagenForm(forms.ModelForm):
    class Meta:
        model = ProductoImagen
        fields = ["imagen", "titulo", "orden", "activa"]

    def clean_imagen(self):
        return validate_image_upload(self.cleaned_data.get("imagen"))


class ProductoCampoForm(forms.ModelForm):
    def __init__(self, *args, producto=None, **kwargs):
        super().__init__(*args, **kwargs)
        if producto is not None and not self.instance.pk:
            self.instance.producto = producto
        if self.instance.pk and self.instance.campo_maestro_id:
            self.fields["tipo"].disabled = True

    class Meta:
        model = ProductoCampo
        fields = [
            "etiqueta", "nombre_interno", "tipo", "obligatorio", "orden",
            "ayuda", "placeholder", "valor_fijo", "activo", "afecta_area_ancho",
            "afecta_area_alto", "es_cantidad",
        ]


class CampoMaestroForm(forms.ModelForm):
    class Meta:
        model = CampoMaestro
        fields = [
            "nombre", "slug", "tipo", "etiqueta_base", "ayuda_base",
            "placeholder_base", "valor_fijo_base", "obligatorio_base", "orden_base", "activo",
        ]


class CampoOpcionForm(forms.ModelForm):
    def __init__(self, *args, campo=None, **kwargs):
        super().__init__(*args, **kwargs)
        if campo is not None and not self.instance.pk:
            self.instance.campo = campo

    class Meta:
        model = CampoOpcion
        fields = ["etiqueta", "valor", "ajuste_tipo", "precio", "orden", "activa"]


class CampoMaestroOpcionForm(forms.ModelForm):
    def __init__(self, *args, campo_maestro=None, **kwargs):
        super().__init__(*args, **kwargs)
        if campo_maestro is not None and not self.instance.pk:
            self.instance.campo_maestro = campo_maestro

    class Meta:
        model = CampoMaestroOpcion
        fields = ["etiqueta", "valor", "ajuste_tipo", "precio", "orden", "activa"]


class SolicitudEstadoForm(forms.ModelForm):
    class Meta:
        model = Solicitud
        fields = ["estado", "precio_final", "notas_internas"]
        widgets = {
            "notas_internas": forms.Textarea(attrs={"rows": 5}),
        }


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = [
            "tipo_cliente", "nombre", "razon_social", "tipo_identificacion",
            "identificacion", "email", "telefono", "whatsapp", "direccion",
            "ciudad", "contacto_principal", "notas", "activo",
        ]
        widgets = {
            "notas": forms.Textarea(attrs={"rows": 4}),
        }


class ClienteContactoForm(forms.ModelForm):
    def __init__(self, *args, cliente=None, **kwargs):
        super().__init__(*args, **kwargs)
        if cliente is not None and not self.instance.pk:
            self.instance.cliente = cliente

    class Meta:
        model = ClienteContacto
        fields = ["nombre", "cargo", "email", "telefono", "whatsapp", "es_principal", "activo", "notas"]
        widgets = {
            "notas": forms.Textarea(attrs={"rows": 3}),
        }


class ProyectoForm(forms.ModelForm):
    class Meta:
        model = Proyecto
        fields = [
            "cliente", "nombre", "cliente_nombre", "cliente_contacto", "cliente_telefono", "cliente_email",
            "descripcion", "estado", "prioridad", "fecha_inicio", "fecha_compromiso",
            "fecha_cierre", "responsable", "activo", "observaciones",
        ]
        widgets = {
            "descripcion": forms.Textarea(attrs={"rows": 4}),
            "observaciones": forms.Textarea(attrs={"rows": 4}),
            "fecha_inicio": forms.DateInput(attrs={"type": "date"}),
            "fecha_compromiso": forms.DateInput(attrs={"type": "date"}),
            "fecha_cierre": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["responsable"].queryset = EmpleadoPerfil.objects.filter(
            activo=True,
            user__is_active=True,
        ).select_related("user")
        self.fields["responsable"].required = False
        clientes = Cliente.objects.filter(activo=True)
        if self.instance.pk and self.instance.cliente_id:
            clientes = Cliente.objects.filter(models.Q(activo=True) | models.Q(pk=self.instance.cliente_id))
        self.fields["cliente"].queryset = clientes.order_by("nombre", "razon_social")
        self.fields["cliente"].required = False


class ProyectoSolicitudForm(forms.Form):
    solicitudes = forms.ModelMultipleChoiceField(
        label="Solicitudes disponibles",
        queryset=Solicitud.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    def __init__(self, *args, proyecto=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.proyecto = proyecto
        queryset = Solicitud.objects.filter(proyecto__isnull=True).select_related("producto").order_by("-creado")
        self.fields["solicitudes"].queryset = queryset


class SolicitudProyectoForm(forms.ModelForm):
    class Meta:
        model = Solicitud
        fields = ["proyecto"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["proyecto"].queryset = Proyecto.objects.filter(activo=True).order_by("-fecha_creacion", "nombre")
        self.fields["proyecto"].required = False


class SolicitudClienteForm(forms.ModelForm):
    class Meta:
        model = Solicitud
        fields = ["cliente"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        clientes = Cliente.objects.filter(activo=True)
        if self.instance.pk and self.instance.cliente_id:
            clientes = Cliente.objects.filter(models.Q(activo=True) | models.Q(pk=self.instance.cliente_id))
        self.fields["cliente"].queryset = clientes.order_by("nombre", "razon_social")
        self.fields["cliente"].required = False


class EmpleadoPerfilForm(forms.Form):
    username = forms.CharField(label="Usuario", max_length=150)
    first_name = forms.CharField(label="Nombre", max_length=150, required=False)
    last_name = forms.CharField(label="Apellido", max_length=150, required=False)
    email = forms.EmailField(label="Email", required=False)
    password1 = forms.CharField(label="Contrasena", required=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirmar contrasena", required=False, widget=forms.PasswordInput)
    is_staff = forms.BooleanField(label="Usuario staff/admin", required=False)
    user_is_active = forms.BooleanField(label="Usuario activo para login", required=False, initial=True)
    telefono = forms.CharField(label="Telefono", max_length=40, required=False)
    cargo = forms.CharField(label="Cargo", max_length=120, required=False)
    area = forms.ChoiceField(label="Area", choices=EmpleadoPerfil.AREAS)
    activo = forms.BooleanField(label="Empleado activo", required=False, initial=True)
    puede_recibir_pedidos = forms.BooleanField(label="Puede recibir pedidos", required=False, initial=True)

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        initial = kwargs.pop("initial", {})
        if instance is not None:
            user = instance.user
            initial.update(
                {
                    "username": user.username,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "email": user.email,
                    "is_staff": user.is_staff,
                    "user_is_active": user.is_active,
                    "telefono": instance.telefono,
                    "cargo": instance.cargo,
                    "area": instance.area,
                    "activo": instance.activo,
                    "puede_recibir_pedidos": instance.puede_recibir_pedidos,
                }
            )
        super().__init__(*args, initial=initial, **kwargs)

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        qs = User.objects.filter(username__iexact=username)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.user_id)
        if qs.exists():
            raise ValidationError("Ya existe un usuario con este nombre.")
        return username

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip()
        if email:
            qs = User.objects.filter(email__iexact=email)
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.user_id)
            if qs.exists():
                raise ValidationError("Ya existe un usuario con este email.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if self.instance is None and not password1:
            self.add_error("password1", "La contrasena inicial es obligatoria.")
        if password1 or password2:
            if password1 != password2:
                self.add_error("password2", "Las contrasenas no coinciden.")
            elif password1:
                try:
                    validate_password(password1, self.instance.user if self.instance else None)
                except ValidationError as exc:
                    self.add_error("password1", exc)
        return cleaned_data

    def save(self):
        data = self.cleaned_data
        if self.instance is None:
            user = User(username=data["username"])
            perfil = EmpleadoPerfil(user=user)
        else:
            perfil = self.instance
            user = perfil.user

        user.username = data["username"]
        user.first_name = data.get("first_name", "")
        user.last_name = data.get("last_name", "")
        user.email = data.get("email", "")
        user.is_staff = data.get("is_staff", False)
        user.is_active = data.get("user_is_active", True)
        if data.get("password1"):
            user.set_password(data["password1"])
        user.save()

        perfil.telefono = data.get("telefono", "")
        perfil.cargo = data.get("cargo", "")
        perfil.area = data.get("area")
        perfil.activo = data.get("activo", False)
        perfil.puede_recibir_pedidos = data.get("puede_recibir_pedidos", False)
        perfil.save()
        return perfil


class SolicitudAsignacionForm(forms.Form):
    empleados = forms.ModelMultipleChoiceField(
        label="Empleados",
        queryset=EmpleadoPerfil.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )
    rol_en_trabajo = forms.CharField(label="Rol en el trabajo", max_length=120, required=False)
    observacion = forms.CharField(label="Observacion", max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["empleados"].queryset = EmpleadoPerfil.objects.filter(
            activo=True,
            puede_recibir_pedidos=True,
            user__is_active=True,
        ).select_related("user").order_by("user__first_name", "user__last_name", "user__username")


class SolicitudTareaForm(forms.ModelForm):
    class Meta:
        model = SolicitudTarea
        fields = [
            "titulo", "descripcion", "responsable", "area", "estado", "prioridad", "orden",
            "fecha_inicio", "fecha_limite", "activa", "requiere_evidencia",
            "evidencia_archivo", "observaciones",
        ]
        widgets = {
            "descripcion": forms.Textarea(attrs={"rows": 4}),
            "observaciones": forms.Textarea(attrs={"rows": 3}),
            "fecha_inicio": forms.DateInput(attrs={"type": "date"}),
            "fecha_limite": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, solicitud=None, **kwargs):
        self.solicitud = solicitud
        super().__init__(*args, **kwargs)
        if solicitud is not None and not self.instance.pk:
            self.instance.solicitud = solicitud
        self.fields["responsable"].queryset = EmpleadoPerfil.objects.filter(
            activo=True,
            puede_recibir_pedidos=True,
            user__is_active=True,
        ).select_related("user").order_by("user__first_name", "user__last_name", "user__username")
        self.fields["responsable"].required = False

    def clean_evidencia_archivo(self):
        return validate_user_upload(self.cleaned_data.get("evidencia_archivo"))


class AdminProduccionEstadoForm(forms.Form):
    estado_produccion = forms.ChoiceField(label="Estado de produccion", choices=Solicitud.ESTADOS_PRODUCCION)
    comentario = forms.CharField(label="Comentario", required=False, widget=forms.Textarea(attrs={"rows": 3}))


class ProduccionEstadoForm(forms.Form):
    ESTADOS_PERMITIDOS = [
        (Solicitud.PROD_EN_PROCESO, "En proceso"),
        (Solicitud.PROD_CON_NOVEDAD, "Con novedad"),
        (Solicitud.PROD_TERMINADO, "Terminado"),
    ]

    estado_produccion = forms.ChoiceField(label="Nuevo estado", choices=ESTADOS_PERMITIDOS)
    comentario = forms.CharField(label="Comentario", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("estado_produccion") == Solicitud.PROD_CON_NOVEDAD and not cleaned_data.get("comentario", "").strip():
            self.add_error("comentario", "Debes agregar un comentario para marcar una novedad.")
        return cleaned_data


class ProduccionTareaEstadoForm(forms.Form):
    ESTADOS_PERMITIDOS = [
        (SolicitudTarea.ESTADO_EN_PROCESO, "En proceso"),
        (SolicitudTarea.ESTADO_BLOQUEADA, "Bloqueada"),
        (SolicitudTarea.ESTADO_TERMINADA, "Terminada"),
    ]

    estado = forms.ChoiceField(label="Nuevo estado", choices=ESTADOS_PERMITIDOS)
    comentario = forms.CharField(label="Comentario", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("estado") == SolicitudTarea.ESTADO_BLOQUEADA and not cleaned_data.get("comentario", "").strip():
            self.add_error("comentario", "Debes agregar un comentario para bloquear una tarea.")
        return cleaned_data


class SolicitudNovedadForm(forms.ModelForm):
    class Meta:
        model = SolicitudNovedad
        fields = ["comentario", "archivo_evidencia"]
        widgets = {
            "comentario": forms.Textarea(attrs={"rows": 4}),
        }

    def clean_archivo_evidencia(self):
        return validate_user_upload(self.cleaned_data.get("archivo_evidencia"))


class DynamicSolicitudForm(forms.Form):
    cliente_nombre = forms.CharField(label="Nombre completo", max_length=160)
    cliente_celular = forms.CharField(label="WhatsApp / celular", max_length=40)
    cliente_email = forms.EmailField(label="Correo electrónico", required=False)

    def __init__(self, producto, *args, **kwargs):
        self.producto = producto
        super().__init__(*args, **kwargs)
        for campo in producto.campos_activos:
            name = self.field_name(campo)
            attrs = {"placeholder": campo.placeholder} if campo.placeholder else {}
            help_text = campo.ayuda or ""
            required = campo.obligatorio
            label = campo.etiqueta

            if campo.tipo == ProductoCampo.TIPO_TEXTO:
                field = forms.CharField(label=label, required=required, help_text=help_text, max_length=255, widget=forms.TextInput(attrs=attrs))
            elif campo.tipo == ProductoCampo.TIPO_TEXTO_LARGO:
                field = forms.CharField(label=label, required=required, help_text=help_text, widget=forms.Textarea(attrs={**attrs, "rows": 4}))
            elif campo.tipo == ProductoCampo.TIPO_NUMERO:
                field = forms.DecimalField(label=label, required=required, help_text=help_text, min_value=Decimal("0"), decimal_places=2, widget=forms.NumberInput(attrs={**attrs, "step": "0.01"}))
            elif campo.tipo == ProductoCampo.TIPO_ENTERO:
                field = forms.IntegerField(label=label, required=required, help_text=help_text, min_value=0, widget=forms.NumberInput(attrs=attrs))
            elif campo.tipo == ProductoCampo.TIPO_SELECT:
                opciones = [("", "Seleccionar")] + [(str(o.id), self.option_label(o)) for o in campo.opciones.filter(activa=True)]
                field = forms.ChoiceField(label=label, required=required, help_text=help_text, choices=opciones)
            elif campo.tipo == ProductoCampo.TIPO_MULTISELECT:
                opciones = [(str(o.id), self.option_label(o)) for o in campo.opciones.filter(activa=True)]
                field = forms.MultipleChoiceField(label=label, required=required, help_text=help_text, choices=opciones, widget=forms.CheckboxSelectMultiple)
            elif campo.tipo == ProductoCampo.TIPO_CHECKBOX:
                field = forms.BooleanField(label=label, required=False, help_text=help_text)
            elif campo.tipo in [ProductoCampo.TIPO_ARCHIVO, ProductoCampo.TIPO_IMAGEN]:
                validators = [validate_image_upload] if campo.tipo == ProductoCampo.TIPO_IMAGEN else [validate_user_upload]
                field = forms.FileField(label=label, required=required, help_text=help_text, validators=validators)
            elif campo.tipo == ProductoCampo.TIPO_COLOR:
                field = forms.CharField(label=label, required=required, help_text=help_text, widget=forms.TextInput(attrs={"type": "color"}))
            elif campo.tipo == ProductoCampo.TIPO_FECHA:
                field = forms.DateField(label=label, required=required, help_text=help_text, widget=forms.DateInput(attrs={"type": "date"}))
            elif campo.tipo == ProductoCampo.TIPO_VALOR_FIJO:
                field = forms.CharField(
                    label=label,
                    required=False,
                    help_text=help_text,
                    initial=campo.valor_fijo,
                    widget=forms.TextInput(attrs={"readonly": "readonly"}),
                )
            else:
                field = forms.CharField(label=label, required=required, help_text=help_text)

            field.campo = campo
            self.fields[name] = field

    def clean(self):
        cleaned_data = super().clean()
        for campo in self.producto.campos_activos:
            if campo.tipo == ProductoCampo.TIPO_VALOR_FIJO:
                cleaned_data[self.field_name(campo)] = campo.valor_fijo
        return cleaned_data

    @staticmethod
    def field_name(campo):
        return f"campo_{campo.id}"

    @staticmethod
    def option_label(opcion):
        precio = opcion.precio or 0
        if opcion.ajuste_tipo == CampoOpcion.AJUSTE_NINGUNO or precio == 0:
            return opcion.etiqueta
        if opcion.ajuste_tipo == CampoOpcion.AJUSTE_PORCENTAJE:
            return f"{opcion.etiqueta} (+{precio}%)"
        return f"{opcion.etiqueta} (+${precio:,.0f})"

    def _decimal_from_cleaned(self, campo):
        name = self.field_name(campo)
        value = self.cleaned_data.get(name)
        if value in [None, ""]:
            return Decimal("0")
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError):
            return Decimal("0")

    def price_context(self):
        ancho = Decimal("0")
        alto = Decimal("0")
        cantidad = Decimal("1")

        for campo in self.producto.campos_activos:
            value = self._decimal_from_cleaned(campo)
            if campo.afecta_area_ancho:
                ancho = value
            if campo.afecta_area_alto:
                alto = value
            if campo.es_cantidad and value > 0:
                cantidad = value

        area_m2 = Decimal("0")
        if ancho > 0 and alto > 0:
            area_m2 = (ancho * alto) / Decimal("10000")

        base = Decimal("0")
        if self.producto.tipo_calculo == Producto.CALCULO_AREA:
            base = area_m2 * self.producto.precio_base_m2 * cantidad
        elif self.producto.tipo_calculo == Producto.CALCULO_UNIDAD:
            base = self.producto.precio_base_unidad * cantidad

        extras = Decimal("0")
        for campo in self.producto.campos_activos:
            name = self.field_name(campo)
            if campo.tipo == ProductoCampo.TIPO_SELECT:
                option_id = self.cleaned_data.get(name)
                extras += self._option_extra(option_id, base, area_m2, cantidad)
            elif campo.tipo == ProductoCampo.TIPO_MULTISELECT:
                for option_id in self.cleaned_data.get(name, []):
                    extras += self._option_extra(option_id, base, area_m2, cantidad)

        total = base + extras
        return {
            "ancho": ancho,
            "alto": alto,
            "cantidad": cantidad,
            "area_m2": area_m2,
            "base": base,
            "extras": extras,
            "total": total.quantize(Decimal("1")) if total else Decimal("0"),
        }

    def _option_extra(self, option_id, base, area_m2, cantidad):
        if not option_id:
            return Decimal("0")
        try:
            option = CampoOpcion.objects.get(id=option_id, activa=True)
        except CampoOpcion.DoesNotExist:
            return Decimal("0")
        precio = option.precio or Decimal("0")
        if option.ajuste_tipo == CampoOpcion.AJUSTE_FIJO:
            return precio
        if option.ajuste_tipo == CampoOpcion.AJUSTE_POR_M2:
            return precio * area_m2 * cantidad
        if option.ajuste_tipo == CampoOpcion.AJUSTE_POR_UNIDAD:
            return precio * cantidad
        if option.ajuste_tipo == CampoOpcion.AJUSTE_PORCENTAJE:
            return base * (precio / Decimal("100"))
        return Decimal("0")

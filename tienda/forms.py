from decimal import Decimal, InvalidOperation
from pathlib import Path
from django import forms
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import models, transaction
from .models import (
    CampoMaestro,
    CampoMaestroOpcion,
    CampoOpcion,
    Categoria,
    Cliente,
    ClienteContacto,
    ClienteUsuario,
    Cotizacion,
    CotizacionItem,
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


class RelatedDataSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        instance = getattr(value, "instance", None)
        if instance is not None:
            cliente_id = getattr(instance, "cliente_id", None)
            proyecto_id = getattr(instance, "proyecto_id", None)
            if not cliente_id:
                proyecto = getattr(instance, "proyecto", None)
                cliente_id = getattr(proyecto, "cliente_id", None)
            if cliente_id:
                option["attrs"]["data-client-id"] = str(cliente_id)
            if proyecto_id:
                option["attrs"]["data-project-id"] = str(proyecto_id)
        return option


def use_related_data_select(field):
    field.widget = RelatedDataSelect(attrs=field.widget.attrs.copy())


def mark_searchable_select(field, **attrs):
    field.widget.attrs["data-searchable-select"] = "true"
    for key, value in attrs.items():
        field.widget.attrs[key] = value


def selected_form_id(form, field_name):
    if form.is_bound:
        value = form.data.get(form.add_prefix(field_name)) or form.data.get(field_name)
    else:
        value = form.initial.get(field_name)
        if value in [None, ""]:
            value = getattr(form.instance, f"{field_name}_id", None)
    if hasattr(value, "pk"):
        value = value.pk
    if value in [None, ""]:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def include_selected(qs, model, selected_id):
    if not selected_id:
        return qs
    return model.objects.filter(models.Q(pk__in=qs.values("pk")) | models.Q(pk=selected_id)).distinct()


def contactos_de_cliente_qs(cliente_id):
    if not cliente_id:
        return ClienteContacto.objects.none()
    return ClienteContacto.objects.filter(activo=True, cliente_id=cliente_id).select_related("cliente")


def proyectos_de_cliente_qs(cliente_id):
    if not cliente_id:
        return Proyecto.objects.none()
    return Proyecto.objects.filter(activo=True, cliente_id=cliente_id).select_related("cliente")


def solicitudes_de_cliente_qs(cliente_id, proyecto_id=None):
    qs = Solicitud.objects.select_related("producto", "cliente", "proyecto")
    if proyecto_id:
        return qs.filter(proyecto_id=proyecto_id).order_by("-creado")
    if not cliente_id:
        return qs.none()
    return qs.filter(
        models.Q(cliente_id=cliente_id) | models.Q(proyecto__cliente_id=cliente_id)
    ).distinct().order_by("-creado")


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
        fields = [
            "estado", "precio_final", "valor_facturado", "estado_facturacion",
            "numero_factura", "fecha_factura", "notas_internas",
        ]
        widgets = {
            "fecha_factura": forms.DateInput(attrs={"type": "date"}),
            "notas_internas": forms.Textarea(attrs={"rows": 5}),
        }


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = [
            "tipo_cliente", "nombre", "razon_social", "tipo_identificacion",
            "identificacion", "email", "telefono", "whatsapp", "direccion",
            "ciudad", "contacto_principal", "nombre_comercial", "sector",
            "sitio_web", "preferencia_contacto", "notas", "activo",
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


class ClienteRegistroForm(forms.Form):
    email = forms.EmailField(label="Correo electrónico")
    password1 = forms.CharField(label="Contraseña", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirmar contraseña", widget=forms.PasswordInput)
    tipo_cliente = forms.ChoiceField(label="Tipo de cliente", choices=Cliente.TIPO_CHOICES)
    nombre = forms.CharField(label="Nombre completo", max_length=180, required=False)
    razon_social = forms.CharField(label="Razón social", max_length=180, required=False)
    nombre_comercial = forms.CharField(label="Nombre comercial", max_length=180, required=False)
    tipo_identificacion = forms.ChoiceField(
        label="Tipo de identificación",
        choices=[("", "Seleccionar")] + list(Cliente.TIPO_IDENTIFICACION_CHOICES),
        required=False,
    )
    identificacion = forms.CharField(label="Número de identificación", max_length=60, required=False)
    telefono = forms.CharField(label="Teléfono", max_length=40, required=False)
    whatsapp = forms.CharField(label="WhatsApp", max_length=40, required=False)
    ciudad = forms.CharField(label="Ciudad", max_length=120, required=False)
    direccion = forms.CharField(label="Dirección", max_length=255, required=False)
    contacto_principal = forms.CharField(label="Contacto principal", max_length=160, required=False)
    cargo_contacto = forms.CharField(label="Cargo del contacto", max_length=120, required=False)
    sector = forms.CharField(label="Sector / industria", max_length=120, required=False)
    sitio_web = forms.URLField(label="Sitio web", required=False)
    preferencia_contacto = forms.CharField(label="Preferencia de contacto", max_length=80, required=False)
    observaciones = forms.CharField(label="Observaciones iniciales", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    acepta_terminos = forms.BooleanField(label="Acepto el tratamiento de mis datos para gestionar mi cuenta y solicitudes")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(models.Q(username__iexact=email) | models.Q(email__iexact=email)).exists():
            raise ValidationError("Ya existe una cuenta con este correo.")
        if Cliente.objects.filter(email__iexact=email).exists():
            raise ValidationError("Ya existe un cliente registrado con este correo. Contacta a Betta para activar tu acceso.")
        return email

    def clean_identificacion(self):
        identificacion = self.cleaned_data.get("identificacion", "").strip()
        if identificacion and Cliente.objects.filter(identificacion__iexact=identificacion).exists():
            raise ValidationError("Ya existe un cliente con esta identificación.")
        return identificacion

    def clean(self):
        cleaned_data = super().clean()
        tipo = cleaned_data.get("tipo_cliente")
        nombre = cleaned_data.get("nombre", "").strip()
        razon_social = cleaned_data.get("razon_social", "").strip()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")

        if tipo == Cliente.TIPO_EMPRESA and not razon_social:
            self.add_error("razon_social", "La razón social es obligatoria para empresas.")
        if tipo == Cliente.TIPO_PERSONA and not nombre:
            self.add_error("nombre", "El nombre es obligatorio.")
        if password1 != password2:
            self.add_error("password2", "Las contraseñas no coinciden.")
        elif password1:
            try:
                validate_password(password1)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned_data

    def save(self):
        data = self.cleaned_data
        email = data["email"]
        with transaction.atomic():
            user = User.objects.create_user(
                username=email,
                email=email,
                password=data["password1"],
                first_name=data.get("nombre", "")[:150],
                is_staff=False,
                is_active=True,
            )
            cliente = Cliente.objects.create(
                tipo_cliente=data["tipo_cliente"],
                nombre=data.get("nombre") or data.get("razon_social") or email,
                razon_social=data.get("razon_social", ""),
                nombre_comercial=data.get("nombre_comercial", ""),
                tipo_identificacion=data.get("tipo_identificacion", ""),
                identificacion=data.get("identificacion", ""),
                email=email,
                telefono=data.get("telefono", ""),
                whatsapp=data.get("whatsapp", ""),
                direccion=data.get("direccion", ""),
                ciudad=data.get("ciudad", ""),
                contacto_principal=data.get("contacto_principal", ""),
                sector=data.get("sector", ""),
                sitio_web=data.get("sitio_web", ""),
                preferencia_contacto=data.get("preferencia_contacto", ""),
                notas=data.get("observaciones", ""),
            )
            contacto = None
            if data.get("contacto_principal"):
                contacto = ClienteContacto.objects.create(
                    cliente=cliente,
                    nombre=data["contacto_principal"],
                    cargo=data.get("cargo_contacto", ""),
                    email=email,
                    telefono=data.get("telefono", ""),
                    whatsapp=data.get("whatsapp", ""),
                    es_principal=True,
                    activo=True,
                )
            cliente_usuario = ClienteUsuario.objects.create(
                cliente=cliente,
                user=user,
                contacto=contacto,
                activo=True,
            )
        return cliente_usuario


class ClienteLoginForm(forms.Form):
    email = forms.EmailField(label="Correo electrónico")
    password = forms.CharField(label="Contraseña", widget=forms.PasswordInput)

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user = None
        self.cliente_usuario = None
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get("email", "").strip().lower()
        password = cleaned_data.get("password", "")
        if not email or not password:
            return cleaned_data

        user_obj = User.objects.filter(email__iexact=email).first() or User.objects.filter(username__iexact=email).first()
        username = user_obj.username if user_obj else email
        user = authenticate(self.request, username=username, password=password)
        if user is None:
            raise ValidationError("Correo o contraseña inválidos.")

        cliente_usuario = getattr(user, "cliente_usuario", None)
        if not cliente_usuario or not cliente_usuario.activo or not cliente_usuario.cliente.activo or not user.is_active:
            raise ValidationError("Tu acceso al portal no esta activo.")

        self.user = user
        self.cliente_usuario = cliente_usuario
        return cleaned_data


class ClientePerfilForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = ["telefono", "whatsapp", "direccion", "ciudad", "contacto_principal", "preferencia_contacto"]


class ClienteUsuarioPortalForm(forms.Form):
    email = forms.EmailField(label="Correo electrónico")
    first_name = forms.CharField(label="Nombre", max_length=150, required=False)
    last_name = forms.CharField(label="Apellido", max_length=150, required=False)
    password1 = forms.CharField(label="Contraseña temporal", required=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirmar contraseña", required=False, widget=forms.PasswordInput)
    contacto = forms.ModelChoiceField(label="Contacto", queryset=ClienteContacto.objects.none(), required=False)
    activo = forms.BooleanField(label="Acceso activo", required=False, initial=True)
    puede_ver_toda_la_cuenta = forms.BooleanField(label="Puede ver toda la cuenta", required=False)
    puede_ver_proyectos = forms.BooleanField(label="Puede ver proyectos", required=False, initial=True)
    puede_ver_solicitudes = forms.BooleanField(label="Puede ver pedidos", required=False, initial=True)
    puede_ver_facturacion = forms.BooleanField(label="Puede ver facturación", required=False)
    puede_descargar_archivos = forms.BooleanField(label="Puede descargar archivos", required=False, initial=True)
    recibe_notificaciones = forms.BooleanField(label="Recibe notificaciones", required=False, initial=True)

    def __init__(self, *args, cliente=None, instance=None, **kwargs):
        self.cliente = cliente or (instance.cliente if instance else None)
        self.instance = instance
        initial = kwargs.pop("initial", {})
        if instance is not None:
            user = instance.user
            initial.update(
                {
                    "email": user.email or user.username,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "contacto": instance.contacto,
                    "activo": instance.activo,
                    "puede_ver_toda_la_cuenta": instance.puede_ver_toda_la_cuenta,
                    "puede_ver_proyectos": instance.puede_ver_proyectos,
                    "puede_ver_solicitudes": instance.puede_ver_solicitudes,
                    "puede_ver_facturacion": instance.puede_ver_facturacion,
                    "puede_descargar_archivos": instance.puede_descargar_archivos,
                    "recibe_notificaciones": instance.recibe_notificaciones,
                }
            )
        super().__init__(*args, initial=initial, **kwargs)
        if self.cliente is not None:
            self.fields["contacto"].queryset = self.cliente.contactos.filter(activo=True)
        mark_searchable_select(self.fields["contacto"])

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        qs = User.objects.filter(models.Q(email__iexact=email) | models.Q(username__iexact=email))
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.user_id)
        if qs.exists():
            raise ValidationError("Ya existe un usuario con este correo.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if self.instance is None and not password1:
            self.add_error("password1", "La contraseña temporal es obligatoria.")
        if password1 or password2:
            if password1 != password2:
                self.add_error("password2", "Las contraseñas no coinciden.")
            elif password1:
                try:
                    validate_password(password1, self.instance.user if self.instance else None)
                except ValidationError as exc:
                    self.add_error("password1", exc)
        contacto = cleaned_data.get("contacto")
        if contacto and self.cliente and contacto.cliente_id != self.cliente.id:
            self.add_error("contacto", "El contacto no pertenece a este cliente.")
        return cleaned_data

    def save(self):
        data = self.cleaned_data
        if self.instance is None:
            user = User(username=data["email"], email=data["email"], is_staff=False, is_active=True)
            cliente_usuario = ClienteUsuario(cliente=self.cliente, user=user)
        else:
            cliente_usuario = self.instance
            user = cliente_usuario.user

        user.username = data["email"]
        user.email = data["email"]
        user.first_name = data.get("first_name", "")
        user.last_name = data.get("last_name", "")
        user.is_staff = False
        user.is_active = True
        if data.get("password1"):
            user.set_password(data["password1"])
        user.save()

        cliente_usuario.contacto = data.get("contacto")
        cliente_usuario.activo = data.get("activo", False)
        cliente_usuario.puede_ver_toda_la_cuenta = data.get("puede_ver_toda_la_cuenta", False)
        cliente_usuario.puede_ver_proyectos = data.get("puede_ver_proyectos", False)
        cliente_usuario.puede_ver_solicitudes = data.get("puede_ver_solicitudes", False)
        cliente_usuario.puede_ver_facturacion = data.get("puede_ver_facturacion", False)
        cliente_usuario.puede_descargar_archivos = data.get("puede_descargar_archivos", False)
        cliente_usuario.recibe_notificaciones = data.get("recibe_notificaciones", False)
        cliente_usuario.save()
        return cliente_usuario


class ClientePasswordResetForm(PasswordResetForm):
    def get_users(self, email):
        users = User.objects.filter(
            models.Q(email__iexact=email) | models.Q(username__iexact=email),
            is_active=True,
            cliente_usuario__activo=True,
            cliente_usuario__cliente__activo=True,
        )
        return (user for user in users if user.has_usable_password())


class CotizacionForm(forms.ModelForm):
    class Meta:
        model = Cotizacion
        fields = [
            "cliente", "contacto", "proyecto", "solicitud", "titulo", "descripcion",
            "fecha_emision", "fecha_vencimiento", "estado", "moneda",
            "observaciones_cliente", "condiciones_comerciales", "tiempo_entrega",
            "forma_pago", "garantia", "validez_dias", "activa",
        ]
        widgets = {
            "fecha_emision": forms.DateInput(attrs={"type": "date"}),
            "fecha_vencimiento": forms.DateInput(attrs={"type": "date"}),
            "descripcion": forms.Textarea(attrs={"rows": 3}),
            "observaciones_cliente": forms.Textarea(attrs={"rows": 3}),
            "condiciones_comerciales": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cliente_id = selected_form_id(self, "cliente")
        contacto_id = selected_form_id(self, "contacto")
        proyecto_id = selected_form_id(self, "proyecto")
        solicitud_id = selected_form_id(self, "solicitud")

        self.fields["cliente"].queryset = Cliente.objects.filter(activo=True).order_by("nombre", "razon_social")
        if self.instance.pk and self.instance.cliente_id:
            self.fields["cliente"].queryset = Cliente.objects.filter(
                models.Q(activo=True) | models.Q(pk=self.instance.cliente_id)
            ).order_by("nombre", "razon_social")
        contactos = contactos_de_cliente_qs(cliente_id).order_by("-es_principal", "nombre")
        proyectos = proyectos_de_cliente_qs(cliente_id).order_by("-fecha_creacion", "nombre")
        solicitudes = solicitudes_de_cliente_qs(cliente_id, proyecto_id)
        self.fields["contacto"].queryset = include_selected(contactos, ClienteContacto, contacto_id)
        self.fields["proyecto"].queryset = include_selected(proyectos, Proyecto, proyecto_id)
        self.fields["solicitud"].queryset = include_selected(solicitudes, Solicitud, solicitud_id)
        self.fields["contacto"].required = False
        self.fields["proyecto"].required = False
        self.fields["solicitud"].required = False
        use_related_data_select(self.fields["contacto"])
        use_related_data_select(self.fields["proyecto"])
        use_related_data_select(self.fields["solicitud"])
        mark_searchable_select(self.fields["cliente"])
        mark_searchable_select(
            self.fields["contacto"],
            **{
                "data-filter-client-source": "id_cliente",
                "data-always-searchable": "true",
                "data-empty-label": "Sin contactos disponibles",
                "data-prefer-principal": "true",
            },
        )
        mark_searchable_select(
            self.fields["proyecto"],
            **{
                "data-filter-client-source": "id_cliente",
                "data-always-searchable": "true",
                "data-empty-label": "Sin proyectos disponibles",
            },
        )
        mark_searchable_select(
            self.fields["solicitud"],
            **{
                "data-filter-client-source": "id_cliente",
                "data-filter-project-source": "id_proyecto",
                "data-always-searchable": "true",
                "data-empty-label": "Sin solicitudes disponibles",
            },
        )

    def clean(self):
        cleaned_data = super().clean()
        cliente = cleaned_data.get("cliente")
        contacto = cleaned_data.get("contacto")
        proyecto = cleaned_data.get("proyecto")
        solicitud = cleaned_data.get("solicitud")
        if contacto and cliente and contacto.cliente_id != cliente.id:
            self.add_error("contacto", "El contacto no pertenece al cliente seleccionado.")
        if proyecto and cliente and proyecto.cliente_id != cliente.id:
            self.add_error("proyecto", "El proyecto pertenece a otro cliente.")
        if solicitud and cliente:
            solicitud_cliente_id = solicitud.cliente_id or getattr(solicitud.proyecto, "cliente_id", None)
            if solicitud_cliente_id != cliente.id:
                self.add_error("solicitud", "La solicitud pertenece a otro cliente.")
        if solicitud and proyecto and solicitud.proyecto_id != proyecto.id:
            self.add_error("solicitud", "La solicitud pertenece a otro proyecto.")
        return cleaned_data


class CotizacionItemForm(forms.ModelForm):
    class Meta:
        model = CotizacionItem
        fields = [
            "producto", "descripcion", "detalle", "cantidad", "unidad", "valor_unitario",
            "descuento_porcentaje", "descuento_valor", "impuesto_porcentaje", "orden", "activo",
        ]
        widgets = {
            "detalle": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, cotizacion=None, **kwargs):
        self.cotizacion = cotizacion
        super().__init__(*args, **kwargs)
        self.fields["producto"].queryset = Producto.objects.filter(activo=True).select_related("categoria")
        self.fields["producto"].required = False
        mark_searchable_select(self.fields["producto"])
        if cotizacion is not None and not self.instance.pk:
            self.instance.cotizacion = cotizacion

    def save(self, commit=True):
        item = super().save(commit=False)
        if self.cotizacion is not None and not item.cotizacion_id:
            item.cotizacion = self.cotizacion
        producto = self.cleaned_data.get("producto")
        if producto:
            if not item.descripcion:
                item.descripcion = producto.nombre
            if not item.detalle:
                item.detalle = producto.descripcion_corta or producto.descripcion_larga[:240]
            if not item.valor_unitario:
                item.valor_unitario = producto.precio_base_unidad or producto.precio_base_m2 or Decimal("0")
        if commit:
            item.save()
        return item


class CotizacionEstadoForm(forms.Form):
    estado = forms.ChoiceField(label="Nuevo estado", choices=Cotizacion.ESTADOS)
    comentario = forms.CharField(label="Comentario interno", required=False, widget=forms.Textarea(attrs={"rows": 3}))


class ProyectoForm(forms.ModelForm):
    class Meta:
        model = Proyecto
        fields = [
            "cliente", "contacto", "nombre", "cliente_nombre", "cliente_contacto", "cliente_telefono", "cliente_email",
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
        cliente_id = selected_form_id(self, "cliente")
        contacto_id = selected_form_id(self, "contacto")
        self.fields["responsable"].queryset = EmpleadoPerfil.objects.filter(
            activo=True,
            user__is_active=True,
        ).select_related("user")
        self.fields["responsable"].required = False
        mark_searchable_select(self.fields["responsable"])
        clientes = Cliente.objects.filter(activo=True)
        if self.instance.pk and self.instance.cliente_id:
            clientes = Cliente.objects.filter(models.Q(activo=True) | models.Q(pk=self.instance.cliente_id))
        self.fields["cliente"].queryset = clientes.order_by("nombre", "razon_social")
        self.fields["cliente"].required = False
        contactos = contactos_de_cliente_qs(cliente_id).order_by("-es_principal", "nombre")
        self.fields["contacto"].queryset = include_selected(contactos, ClienteContacto, contacto_id)
        self.fields["contacto"].required = False
        use_related_data_select(self.fields["contacto"])
        mark_searchable_select(self.fields["cliente"])
        mark_searchable_select(
            self.fields["contacto"],
            **{
                "data-filter-client-source": "id_cliente",
                "data-always-searchable": "true",
                "data-empty-label": "Sin contactos disponibles",
                "data-prefer-principal": "true",
            },
        )

    def clean(self):
        cleaned_data = super().clean()
        cliente = cleaned_data.get("cliente")
        contacto = cleaned_data.get("contacto")
        if contacto and cliente and contacto.cliente_id != cliente.id:
            self.add_error("contacto", "El contacto no pertenece al cliente seleccionado.")
        if contacto and not cliente:
            self.add_error("contacto", "Selecciona un cliente para asociar este contacto.")
        return cleaned_data


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
        if proyecto and proyecto.cliente_id:
            queryset = queryset.filter(models.Q(cliente__isnull=True) | models.Q(cliente=proyecto.cliente))
        if proyecto and proyecto.contacto_id:
            queryset = queryset.filter(models.Q(contacto__isnull=True) | models.Q(contacto=proyecto.contacto))
        self.fields["solicitudes"].queryset = queryset


class SolicitudProyectoForm(forms.ModelForm):
    class Meta:
        model = Solicitud
        fields = ["proyecto"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["proyecto"].queryset = Proyecto.objects.filter(activo=True).select_related("cliente").order_by("-fecha_creacion", "nombre")
        self.fields["proyecto"].required = False
        use_related_data_select(self.fields["proyecto"])
        mark_searchable_select(self.fields["proyecto"])


class SolicitudClienteForm(forms.ModelForm):
    class Meta:
        model = Solicitud
        fields = ["cliente", "contacto"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        clientes = Cliente.objects.filter(activo=True)
        if self.instance.pk and self.instance.cliente_id:
            clientes = Cliente.objects.filter(models.Q(activo=True) | models.Q(pk=self.instance.cliente_id))
        self.fields["cliente"].queryset = clientes.order_by("nombre", "razon_social")
        self.fields["cliente"].required = False
        self.fields["contacto"].queryset = ClienteContacto.objects.filter(activo=True).select_related("cliente")
        self.fields["contacto"].required = False
        use_related_data_select(self.fields["contacto"])
        mark_searchable_select(self.fields["cliente"])
        mark_searchable_select(self.fields["contacto"], **{"data-filter-client-source": "id_cliente"})

    def clean(self):
        cleaned_data = super().clean()
        cliente = cleaned_data.get("cliente")
        contacto = cleaned_data.get("contacto")
        proyecto = self.instance.proyecto if self.instance and self.instance.pk else None
        cliente_esperado = cliente or getattr(proyecto, "cliente", None)
        if contacto and cliente_esperado and contacto.cliente_id != cliente_esperado.id:
            self.add_error("contacto", "El contacto no pertenece al cliente de la solicitud.")
        if contacto and not cliente_esperado:
            self.add_error("contacto", "Selecciona un cliente o proyecto para asociar este contacto.")
        return cleaned_data


class EmpleadoPerfilForm(forms.Form):
    username = forms.CharField(label="Usuario", max_length=150)
    first_name = forms.CharField(label="Nombre", max_length=150, required=False)
    last_name = forms.CharField(label="Apellido", max_length=150, required=False)
    email = forms.EmailField(label="Email", required=False)
    password1 = forms.CharField(label="Contraseña", required=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirmar contraseña", required=False, widget=forms.PasswordInput)
    is_staff = forms.BooleanField(label="Usuario staff/admin", required=False)
    user_is_active = forms.BooleanField(label="Usuario activo para login", required=False, initial=True)
    telefono = forms.CharField(label="Teléfono", max_length=40, required=False)
    cargo = forms.CharField(label="Cargo", max_length=120, required=False)
    area = forms.ChoiceField(label="Área", choices=EmpleadoPerfil.AREAS)
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
            self.add_error("password1", "La contraseña inicial es obligatoria.")
        if password1 or password2:
            if password1 != password2:
                self.add_error("password2", "Las contraseñas no coinciden.")
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
    observacion = forms.CharField(label="Observación", max_length=255, required=False)

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
        mark_searchable_select(self.fields["responsable"])

    def clean_evidencia_archivo(self):
        return validate_user_upload(self.cleaned_data.get("evidencia_archivo"))


class AdminProduccionEstadoForm(forms.Form):
    estado_produccion = forms.ChoiceField(label="Estado de producción", choices=Solicitud.ESTADOS_PRODUCCION)
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
        fields = ["comentario", "archivo_evidencia", "visible_para_cliente"]
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
        self.cliente_usuario = kwargs.pop("cliente_usuario", None)
        super().__init__(*args, **kwargs)
        self._setup_cliente_usuario_fields()
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

    def _cliente_usuario_values(self, contacto=None):
        if not self.cliente_usuario:
            return {"nombre": "", "celular": "", "email": ""}
        cliente = self.cliente_usuario.cliente
        contacto = contacto if contacto is not None else self.cliente_usuario.contacto
        nombre = (getattr(contacto, "nombre", "") or cliente.contacto_principal or str(cliente)).strip()
        celular = (
            getattr(contacto, "whatsapp", "")
            or getattr(contacto, "telefono", "")
            or cliente.whatsapp
            or cliente.telefono
            or ""
        )
        email = getattr(contacto, "email", "") or cliente.email or ""
        return {"nombre": nombre, "celular": celular, "email": email}

    def _setup_cliente_usuario_fields(self):
        if not self.cliente_usuario:
            return
        values = self._cliente_usuario_values()
        mapping = {
            "cliente_nombre": values["nombre"],
            "cliente_celular": values["celular"],
            "cliente_email": values["email"],
        }
        for name, value in mapping.items():
            self.fields[name].required = False
            self.fields[name].initial = value
            self.fields[name].widget.attrs.update({"readonly": "readonly", "data-portal-readonly": "true"})
        if self.cliente_usuario.puede_ver_toda_la_cuenta:
            contactos = self.cliente_usuario.cliente.contactos.filter(activo=True)
            self.fields["contacto"] = forms.ModelChoiceField(label="Contacto", queryset=contactos, required=False)
            self.fields["contacto"].initial = self.cliente_usuario.contacto or contactos.filter(es_principal=True).first()
            mark_searchable_select(self.fields["contacto"])

    def resolve_portal_contact(self):
        if not self.cliente_usuario:
            return None
        if self.cliente_usuario.puede_ver_toda_la_cuenta and "contacto" in self.fields:
            return self.cleaned_data.get("contacto") or self.cliente_usuario.contacto
        return self.cliente_usuario.contacto

    def clean(self):
        cleaned_data = super().clean()
        if self.cliente_usuario:
            contacto = cleaned_data.get("contacto")
            if contacto and contacto.cliente_id != self.cliente_usuario.cliente_id:
                self.add_error("contacto", "El contacto no pertenece a tu cuenta.")
                contacto = None
            values = self._cliente_usuario_values(contacto if self.cliente_usuario.puede_ver_toda_la_cuenta else None)
            cleaned_data["cliente_nombre"] = values["nombre"]
            cleaned_data["cliente_celular"] = values["celular"]
            cleaned_data["cliente_email"] = values["email"]
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

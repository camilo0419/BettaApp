from decimal import Decimal, InvalidOperation
from pathlib import Path
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from .models import CampoMaestro, CampoMaestroOpcion, CampoOpcion, Producto, ProductoCampo, ProductoImagen, Solicitud


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
            "ayuda", "placeholder", "activo", "afecta_area_ancho",
            "afecta_area_alto", "es_cantidad",
        ]


class CampoMaestroForm(forms.ModelForm):
    class Meta:
        model = CampoMaestro
        fields = [
            "nombre", "slug", "tipo", "etiqueta_base", "ayuda_base",
            "placeholder_base", "obligatorio_base", "orden_base", "activo",
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
            else:
                field = forms.CharField(label=label, required=required, help_text=help_text)

            field.campo = campo
            self.fields[name] = field

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

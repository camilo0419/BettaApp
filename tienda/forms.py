from decimal import Decimal, InvalidOperation
from django import forms
from .models import CampoOpcion, Producto, ProductoCampo, ProductoImagen, Solicitud


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


class ProductoImagenForm(forms.ModelForm):
    class Meta:
        model = ProductoImagen
        fields = ["imagen", "titulo", "orden", "activa"]


class ProductoCampoForm(forms.ModelForm):
    class Meta:
        model = ProductoCampo
        fields = [
            "etiqueta", "nombre_interno", "tipo", "obligatorio", "orden",
            "ayuda", "placeholder", "activo", "afecta_area_ancho",
            "afecta_area_alto", "es_cantidad",
        ]


class CampoOpcionForm(forms.ModelForm):
    class Meta:
        model = CampoOpcion
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
                field = forms.FileField(label=label, required=required, help_text=help_text)
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

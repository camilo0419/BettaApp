from django import forms
from .models import Cotizacion


class CotizacionForm(forms.ModelForm):
    OPCIONES_DISENO = [
        ('tengo_diseno', 'Tengo mi diseño'),
        ('necesito_diseno', 'Necesito que me diseñen'),
        ('asesoria', 'Quiero asesoría antes de producir'),
    ]

    tipo_diseno = forms.ChoiceField(choices=OPCIONES_DISENO, widget=forms.RadioSelect)
    material = forms.ChoiceField(required=False)
    acabado = forms.ChoiceField(required=False)
    extra = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple)
    iluminacion = forms.ChoiceField(required=False)

    class Meta:
        model = Cotizacion
        fields = [
            'nombre_cliente', 'celular', 'email', 'ancho_cm', 'alto_cm', 'cantidad',
            'texto_personalizado', 'color', 'archivo', 'observaciones'
        ]
        widgets = {
            'nombre_cliente': forms.TextInput(attrs={'placeholder': 'Nombre completo o empresa'}),
            'celular': forms.TextInput(attrs={'placeholder': 'Ej: 300 000 0000'}),
            'email': forms.EmailInput(attrs={'placeholder': 'correo@empresa.com'}),
            'observaciones': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Cuéntanos dónde se instalará, fecha ideal, restricciones o detalles importantes.'}),
            'texto_personalizado': forms.TextInput(attrs={'placeholder': 'Ej: Restaurante La 80 / Promo / Nombre del aviso'}),
            'color': forms.TextInput(attrs={'placeholder': 'Ej: negro, blanco, rojo, azul, naranja'}),
        }

    def __init__(self, *args, producto=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.producto = producto
        for field_name, field in self.fields.items():
            if field_name not in ['tipo_diseno', 'extra']:
                field.widget.attrs.setdefault('class', 'form-control')
        self.fields['tipo_diseno'].widget.attrs.update({'class': 'choice-list'})
        self.fields['extra'].widget.attrs.update({'class': 'choice-list'})
        self.fields['cantidad'].widget.attrs.update({'min': '1'})
        self.fields['ancho_cm'].widget.attrs.update({'step': '0.1', 'min': '1'})
        self.fields['alto_cm'].widget.attrs.update({'step': '0.1', 'min': '1'})

        if producto:
            opciones = producto.opciones.filter(activo=True)
            self.fields['material'].choices = [('', 'Seleccionar material')] + [
                (str(o.id), f'{o.nombre} {self._extra_label(o)}') for o in opciones.filter(tipo='material')
            ]
            self.fields['acabado'].choices = [('', 'Seleccionar acabado')] + [
                (str(o.id), f'{o.nombre} {self._extra_label(o)}') for o in opciones.filter(tipo='acabado')
            ]
            self.fields['extra'].choices = [
                (str(o.id), f'{o.nombre} {self._extra_label(o)}') for o in opciones.filter(tipo='extra')
            ]
            self.fields['iluminacion'].choices = [('', 'Sin iluminación / no aplica')] + [
                (str(o.id), f'{o.nombre} {self._extra_label(o)}') for o in opciones.filter(tipo='iluminacion')
            ]

            if not opciones.filter(tipo='material').exists():
                self.fields.pop('material')
            if not opciones.filter(tipo='acabado').exists():
                self.fields.pop('acabado')
            if not opciones.filter(tipo='extra').exists():
                self.fields.pop('extra')
            if not opciones.filter(tipo='iluminacion').exists():
                self.fields.pop('iluminacion')

            if producto.tipo_calculo in ['unidad', 'estimado']:
                self.fields['ancho_cm'].required = False
                self.fields['alto_cm'].required = False

    def _extra_label(self, opcion):
        if opcion.valor_extra and opcion.valor_extra > 0:
            sufijo = '/m²' if opcion.multiplica_area else ''
            return f'(+${opcion.valor_extra:,.0f}{sufijo})'
        return ''


class CotizacionGestionForm(forms.ModelForm):
    class Meta:
        model = Cotizacion
        fields = [
            'estado', 'nombre_cliente', 'celular', 'email', 'ancho_cm', 'alto_cm',
            'cantidad', 'texto_personalizado', 'color', 'precio_estimado',
            'requiere_revision', 'observaciones', 'notas_internas'
        ]
        widgets = {
            'observaciones': forms.Textarea(attrs={'rows': 4}),
            'notas_internas': forms.Textarea(attrs={'rows': 5, 'placeholder': 'Notas internas para ventas, diseño o producción.'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault('class', 'form-control')

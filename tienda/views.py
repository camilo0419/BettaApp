from decimal import Decimal
from urllib.parse import quote
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy

from .forms import CotizacionForm, CotizacionGestionForm
from .models import Cotizacion, OpcionProducto, Producto


WHATSAPP_EMPRESA = '573026491143'


def money(value):
    return f'${int(value or 0):,}'.replace(',', '.')


def calcular_precio(producto, cleaned_data, selected_ids):
    cantidad = cleaned_data.get('cantidad') or 1
    ancho = cleaned_data.get('ancho_cm') or Decimal('0')
    alto = cleaned_data.get('alto_cm') or Decimal('0')
    area = (ancho / Decimal('100')) * (alto / Decimal('100'))
    base_calculo = producto.precio_base

    if producto.tipo_calculo == 'area_m2':
        area_facturable = max(area, producto.minimo_facturable)
        subtotal = area_facturable * base_calculo
    elif producto.tipo_calculo == 'unidad':
        area_facturable = Decimal('1')
        subtotal = base_calculo
    else:
        area_facturable = Decimal('1')
        subtotal = base_calculo

    opciones = OpcionProducto.objects.filter(id__in=selected_ids, producto=producto, activo=True)
    extras = Decimal('0')
    detalle_opciones = []
    for opcion in opciones:
        valor = opcion.valor_extra * (area_facturable if opcion.multiplica_area else 1)
        extras += valor
        detalle_opciones.append({
            'tipo': opcion.tipo,
            'nombre': opcion.nombre,
            'valor': float(valor),
        })

    tipo_diseno = cleaned_data.get('tipo_diseno')
    if tipo_diseno == 'necesito_diseno':
        extras += Decimal('35000')
        detalle_opciones.append({'tipo': 'diseno', 'nombre': 'Diseño básico asistido', 'valor': 35000})
    elif tipo_diseno == 'asesoria':
        detalle_opciones.append({'tipo': 'diseno', 'nombre': 'Asesoría previa sin costo', 'valor': 0})

    total = (subtotal + extras) * cantidad
    requiere_revision = producto.requiere_revision or producto.tipo_calculo == 'estimado'
    return total.quantize(Decimal('1')), area, detalle_opciones, requiere_revision


def home(request):
    productos = Producto.objects.filter(activo=True).order_by('orden', 'nombre')
    return render(request, 'tienda/home.html', {'productos': productos})


def producto_detalle(request, slug):
    producto = get_object_or_404(Producto, slug=slug, activo=True)

    if request.method == 'POST':
        form = CotizacionForm(request.POST, request.FILES, producto=producto)
        if form.is_valid():
            selected_ids = []
            for field in ['material', 'acabado', 'iluminacion']:
                value = form.cleaned_data.get(field)
                if value:
                    selected_ids.append(value)
            selected_ids += form.cleaned_data.get('extra') or []

            precio, area, detalle_opciones, requiere_revision = calcular_precio(producto, form.cleaned_data, selected_ids)
            cotizacion = form.save(commit=False)
            cotizacion.producto = producto
            cotizacion.precio_estimado = precio
            cotizacion.requiere_revision = requiere_revision
            cotizacion.opciones_json = {
                'tipo_diseno': form.cleaned_data.get('tipo_diseno'),
                'area_m2': float(area),
                'opciones': detalle_opciones,
            }
            cotizacion.save()
            messages.success(request, 'Solicitud recibida. Betta Diseño revisará la información antes de producir.')
            return redirect('tienda:cotizacion_exito', pk=cotizacion.pk)
    else:
        form = CotizacionForm(producto=producto, initial={'cantidad': 1})

    return render(request, 'tienda/producto_detalle.html', {'producto': producto, 'form': form})


def cotizacion_exito(request, pk):
    cotizacion = get_object_or_404(Cotizacion, pk=pk)
    texto = (
        f"Hola Betta Diseño, quiero revisar esta solicitud:%0A"
        f"Solicitud: #{cotizacion.id}%0A"
        f"Producto: {cotizacion.producto.nombre}%0A"
        f"Cliente: {cotizacion.nombre_cliente}%0A"
        f"Celular: {cotizacion.celular}%0A"
        f"Medidas: {cotizacion.ancho_cm or '-'} cm x {cotizacion.alto_cm or '-'} cm%0A"
        f"Cantidad: {cotizacion.cantidad}%0A"
        f"Precio estimado: {money(cotizacion.precio_estimado)} COP%0A"
        f"Observaciones: {cotizacion.observaciones or '-'}"
    )
    whatsapp_url = f'https://wa.me/{WHATSAPP_EMPRESA}?text={quote(texto, safe="%:")}'
    return render(request, 'tienda/cotizacion_exito.html', {
        'cotizacion': cotizacion,
        'whatsapp_url': whatsapp_url,
        'precio_formateado': money(cotizacion.precio_estimado),
    })


class PanelLoginView(LoginView):
    template_name = 'tienda/panel/login.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        return reverse_lazy('tienda:panel_dashboard')


@login_required
def panel_dashboard(request):
    qs = Cotizacion.objects.select_related('producto')
    total = qs.count()
    valor_total = qs.aggregate(s=Sum('precio_estimado'))['s'] or 0
    conteos = dict(qs.values_list('estado').annotate(c=Count('id')))
    recientes = qs[:8]
    por_producto = qs.values('producto__nombre').annotate(total=Count('id')).order_by('-total')[:5]
    return render(request, 'tienda/panel/dashboard.html', {
        'total': total,
        'valor_total': money(valor_total),
        'conteos': conteos,
        'recientes': recientes,
        'por_producto': por_producto,
    })


@login_required
def panel_cotizaciones(request):
    qs = Cotizacion.objects.select_related('producto')
    estado = request.GET.get('estado', '')
    q = request.GET.get('q', '').strip()
    if estado:
        qs = qs.filter(estado=estado)
    if q:
        qs = qs.filter(
            Q(nombre_cliente__icontains=q) |
            Q(celular__icontains=q) |
            Q(email__icontains=q) |
            Q(producto__nombre__icontains=q) |
            Q(texto_personalizado__icontains=q)
        )
    return render(request, 'tienda/panel/cotizaciones.html', {
        'cotizaciones': qs[:200],
        'estados': Cotizacion.ESTADOS,
        'estado_actual': estado,
        'q': q,
    })


@login_required
def panel_cotizacion_detalle(request, pk):
    cotizacion = get_object_or_404(Cotizacion.objects.select_related('producto'), pk=pk)
    if request.method == 'POST':
        nuevo_estado = request.POST.get('estado')
        if nuevo_estado in dict(Cotizacion.ESTADOS):
            cotizacion.estado = nuevo_estado
            cotizacion.save(update_fields=['estado', 'actualizado'])
            messages.success(request, f'Solicitud #{cotizacion.id} movida a: {cotizacion.get_estado_display()}')
        return redirect('tienda:panel_cotizacion_detalle', pk=cotizacion.pk)
    return render(request, 'tienda/panel/cotizacion_detalle.html', {
        'cotizacion': cotizacion,
        'estados': Cotizacion.ESTADOS,
    })


@login_required
def panel_cotizacion_editar(request, pk):
    cotizacion = get_object_or_404(Cotizacion.objects.select_related('producto'), pk=pk)
    if request.method == 'POST':
        form = CotizacionGestionForm(request.POST, request.FILES, instance=cotizacion)
        if form.is_valid():
            form.save()
            messages.success(request, f'Solicitud #{cotizacion.id} actualizada correctamente.')
            return redirect('tienda:panel_cotizacion_detalle', pk=cotizacion.pk)
    else:
        form = CotizacionGestionForm(instance=cotizacion)
    return render(request, 'tienda/panel/cotizacion_editar.html', {'cotizacion': cotizacion, 'form': form})

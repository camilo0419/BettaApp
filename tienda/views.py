from decimal import Decimal
from urllib.parse import quote
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.db.models import Count, Q, Sum
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import (
    CampoOpcionForm,
    DynamicSolicitudForm,
    ProductoCampoForm,
    ProductoForm,
    ProductoImagenForm,
    SolicitudEstadoForm,
)
from .models import CampoOpcion, Producto, ProductoCampo, ProductoImagen, Solicitud, SolicitudRespuesta

WHATSAPP_EMPRESA = "573026491143" 


def home(request):
    productos = Producto.objects.filter(activo=True, destacado=True).order_by("orden", "nombre")
    return render(request, "tienda/home.html", {"productos": productos})


def producto_detalle(request, slug):
    producto = get_object_or_404(Producto, slug=slug, activo=True)
    campos = producto.campos_activos.prefetch_related("opciones")
    galeria = producto.imagenes.filter(activa=True)

    if request.method == "POST":
        form = DynamicSolicitudForm(producto, request.POST, request.FILES)
        if form.is_valid():
            ctx = form.price_context()
            requiere_revision = producto.requiere_revision or producto.tipo_calculo == Producto.CALCULO_MANUAL
            solicitud = Solicitud.objects.create(
                producto=producto,
                cliente_nombre=form.cleaned_data["cliente_nombre"],
                cliente_celular=form.cleaned_data["cliente_celular"],
                cliente_email=form.cleaned_data.get("cliente_email", ""),
                precio_estimado=ctx["total"],
                requiere_revision=requiere_revision,
            )

            for campo in campos:
                name = form.field_name(campo)
                value = form.cleaned_data.get(name)
                archivo = None
                valor_texto = ""
                if campo.tipo in [ProductoCampo.TIPO_ARCHIVO, ProductoCampo.TIPO_IMAGEN]:
                    archivo = request.FILES.get(name)
                    valor_texto = archivo.name if archivo else ""
                elif campo.tipo == ProductoCampo.TIPO_SELECT:
                    try:
                        opcion = CampoOpcion.objects.get(id=value)
                        valor_texto = opcion.etiqueta
                    except Exception:
                        valor_texto = ""
                elif campo.tipo == ProductoCampo.TIPO_MULTISELECT:
                    labels = list(CampoOpcion.objects.filter(id__in=value).values_list("etiqueta", flat=True)) if value else []
                    valor_texto = ", ".join(labels)
                elif campo.tipo == ProductoCampo.TIPO_CHECKBOX:
                    valor_texto = "Sí" if value else "No"
                else:
                    valor_texto = str(value) if value not in [None, ""] else ""

                if valor_texto or archivo:
                    SolicitudRespuesta.objects.create(
                        solicitud=solicitud,
                        campo=campo,
                        etiqueta=campo.etiqueta,
                        tipo=campo.tipo,
                        valor_texto=valor_texto,
                        archivo=archivo,
                        orden=campo.orden,
                    )

            return redirect("cotizacion_exito", pk=solicitud.pk)
    else:
        form = DynamicSolicitudForm(producto)

    return render(request, "tienda/producto_detalle.html", {"producto": producto, "campos": campos, "galeria": galeria, "form": form})


def cotizacion_exito(request, pk):
    solicitud = get_object_or_404(Solicitud, pk=pk)
    partes = [
        f"Hola Betta Diseño, quiero cotizar el producto: {solicitud.producto.nombre}",
        f"Solicitud #{solicitud.id}",
        f"Nombre: {solicitud.cliente_nombre}",
        f"Celular: {solicitud.cliente_celular}",
    ]
    if solicitud.precio_estimado and not solicitud.requiere_revision:
        partes.append(f"Precio estimado: ${solicitud.precio_estimado:,.0f}")
    else:
        partes.append("Precio sujeto a revisión del equipo Betta Diseño")
    mensaje = quote("\n".join(partes))
    whatsapp_url = f"https://wa.me/{WHATSAPP_EMPRESA}?text={mensaje}"
    return render(request, "tienda/cotizacion_exito.html", {"solicitud": solicitud, "whatsapp_url": whatsapp_url})


def panel_login(request):
    if request.user.is_authenticated:
        return redirect("panel_dashboard")
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        return redirect("panel_dashboard")
    return render(request, "tienda/panel/login.html", {"form": form})


def panel_logout(request):
    logout(request)
    return redirect("panel_login")


@login_required
def panel_dashboard(request):
    total = Solicitud.objects.count()
    nuevas = Solicitud.objects.filter(estado=Solicitud.ESTADO_NUEVA).count()
    revision = Solicitud.objects.filter(estado__in=[Solicitud.ESTADO_REVISION, Solicitud.ESTADO_PENDIENTE_INFO]).count()
    productos = Producto.objects.count()
    recientes = Solicitud.objects.select_related("producto").order_by("-creado")[:8]
    por_estado = Solicitud.objects.values("estado").annotate(total=Count("id")).order_by("estado")
    return render(request, "tienda/panel/dashboard.html", {"total": total, "nuevas": nuevas, "revision": revision, "productos": productos, "recientes": recientes, "por_estado": por_estado})


@login_required
def solicitudes_lista(request):
    q = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "").strip()
    solicitudes = Solicitud.objects.select_related("producto").all()
    if q:
        
        filtro = Q(cliente_nombre__icontains=q) | Q(cliente_celular__icontains=q) | Q(producto__nombre__icontains=q)
        if q.isdigit():
            filtro = filtro | Q(id=int(q))
        solicitudes = solicitudes.filter(filtro)
    if estado:
        solicitudes = solicitudes.filter(estado=estado)
    return render(request, "tienda/panel/solicitudes.html", {"solicitudes": solicitudes, "estados": Solicitud.ESTADOS, "q": q, "estado": estado})


@login_required
def solicitud_detalle(request, pk):
    solicitud = get_object_or_404(Solicitud.objects.select_related("producto"), pk=pk)
    if request.method == "POST":
        form = SolicitudEstadoForm(request.POST, instance=solicitud)
        if form.is_valid():
            form.save()
            messages.success(request, "Solicitud actualizada correctamente.")
            return redirect("panel_solicitud_detalle", pk=solicitud.pk)
    else:
        form = SolicitudEstadoForm(instance=solicitud)
    return render(request, "tienda/panel/solicitud_detalle.html", {"solicitud": solicitud, "form": form})


@login_required
def productos_lista(request):
    productos = Producto.objects.annotate(num_campos=Count("campos", distinct=True), num_solicitudes=Count("solicitudes", distinct=True)).order_by("orden", "nombre")
    return render(request, "tienda/panel/productos.html", {"productos": productos})


@login_required
def producto_crear(request):
    form = ProductoForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        producto = form.save()
        messages.success(request, "Producto creado. Ahora configura sus campos y opciones.")
        return redirect("panel_producto_campos", producto_id=producto.id)
    return render(request, "tienda/panel/producto_form.html", {"form": form, "titulo": "Crear producto"})


@login_required
def producto_editar(request, producto_id):
    producto = get_object_or_404(Producto, pk=producto_id)
    form = ProductoForm(request.POST or None, request.FILES or None, instance=producto)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Producto actualizado.")
        return redirect("panel_productos")
    return render(request, "tienda/panel/producto_form.html", {"form": form, "producto": producto, "titulo": "Editar producto"})


@login_required
def producto_campos(request, producto_id):
    producto = get_object_or_404(Producto, pk=producto_id)
    campos = producto.campos.prefetch_related("opciones").order_by("orden", "id")
    return render(request, "tienda/panel/producto_campos.html", {"producto": producto, "campos": campos})


@login_required
def campo_crear(request, producto_id):
    producto = get_object_or_404(Producto, pk=producto_id)
    form = ProductoCampoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        campo = form.save(commit=False)
        campo.producto = producto
        campo.save()
        messages.success(request, "Campo creado.")
        return redirect("panel_producto_campos", producto_id=producto.id)
    return render(request, "tienda/panel/campo_form.html", {"form": form, "producto": producto, "titulo": "Crear campo"})


@login_required
def campo_editar(request, campo_id):
    campo = get_object_or_404(ProductoCampo.objects.select_related("producto"), pk=campo_id)
    form = ProductoCampoForm(request.POST or None, instance=campo)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Campo actualizado.")
        return redirect("panel_producto_campos", producto_id=campo.producto.id)
    return render(request, "tienda/panel/campo_form.html", {"form": form, "producto": campo.producto, "campo": campo, "titulo": "Editar campo"})


@login_required
def campo_opciones(request, campo_id):
    campo = get_object_or_404(ProductoCampo.objects.select_related("producto"), pk=campo_id)
    opciones = campo.opciones.order_by("orden", "id")
    return render(request, "tienda/panel/campo_opciones.html", {"campo": campo, "opciones": opciones})


@login_required
def opcion_crear(request, campo_id):
    campo = get_object_or_404(ProductoCampo.objects.select_related("producto"), pk=campo_id)
    form = CampoOpcionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        opcion = form.save(commit=False)
        opcion.campo = campo
        opcion.save()
        messages.success(request, "Opción creada.")
        return redirect("panel_campo_opciones", campo_id=campo.id)
    return render(request, "tienda/panel/opcion_form.html", {"form": form, "campo": campo, "titulo": "Crear opción"})


@login_required
def opcion_editar(request, opcion_id):
    opcion = get_object_or_404(CampoOpcion.objects.select_related("campo__producto"), pk=opcion_id)
    form = CampoOpcionForm(request.POST or None, instance=opcion)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Opción actualizada.")
        return redirect("panel_campo_opciones", campo_id=opcion.campo.id)
    return render(request, "tienda/panel/opcion_form.html", {"form": form, "campo": opcion.campo, "opcion": opcion, "titulo": "Editar opción"})


@login_required
def producto_imagenes(request, producto_id):
    producto = get_object_or_404(Producto, pk=producto_id)
    form = ProductoImagenForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        img = form.save(commit=False)
        img.producto = producto
        img.save()
        messages.success(request, "Imagen agregada.")
        return redirect("panel_producto_imagenes", producto_id=producto.id)
    imagenes = producto.imagenes.order_by("orden", "id")
    return render(request, "tienda/panel/producto_imagenes.html", {"producto": producto, "form": form, "imagenes": imagenes})


@login_required
@require_POST
def producto_toggle(request, producto_id):
    producto = get_object_or_404(Producto, pk=producto_id)
    producto.activo = not producto.activo
    producto.save(update_fields=["activo"])
    return redirect("panel_productos")


@login_required
@require_POST
def imagen_eliminar(request, imagen_id):
    imagen = get_object_or_404(ProductoImagen, pk=imagen_id)
    producto_id = imagen.producto_id
    imagen.delete()
    messages.success(request, "Imagen eliminada.")
    return redirect("panel_producto_imagenes", producto_id=producto_id)

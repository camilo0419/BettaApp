from functools import wraps
from urllib.parse import quote
from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import (
    CampoMaestroForm,
    CampoMaestroOpcionForm,
    CampoOpcionForm,
    DynamicSolicitudForm,
    ProductoCampoForm,
    ProductoForm,
    ProductoImagenForm,
    SolicitudEstadoForm,
)
from .models import Categoria, CampoMaestro, CampoMaestroOpcion, CampoOpcion, Producto, ProductoCampo, ProductoImagen, Solicitud, SolicitudRespuesta

WHATSAPP_EMPRESA = "573026491143" 
COTIZACION_TOKEN_SALT = "tienda.cotizacion_exito"


def panel_staff_required(view_func):
    @login_required(login_url="panel_login")
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper


def solicitud_token(solicitud_id):
    return signing.dumps({"solicitud_id": solicitud_id}, salt=COTIZACION_TOKEN_SALT)


def validate_solicitud_token(pk, token):
    try:
        data = signing.loads(token, salt=COTIZACION_TOKEN_SALT)
    except signing.BadSignature as exc:
        raise Http404 from exc

    if str(data.get("solicitud_id")) != str(pk):
        raise Http404


def safe_panel_redirect(request):
    redirect_to = request.POST.get(REDIRECT_FIELD_NAME) or request.GET.get(REDIRECT_FIELD_NAME)
    if redirect_to and url_has_allowed_host_and_scheme(
        redirect_to,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect_to
    return reverse("panel_dashboard")


def validation_message(error):
    if hasattr(error, "messages"):
        return " ".join(error.messages)
    return str(error)


def home(request):
    productos = (
        Producto.objects.filter(activo=True, destacado=True, categoria__activa=True)
        .select_related("categoria")
        .prefetch_related("imagenes")
        .order_by("orden", "nombre")[:5]
    )
    return render(request, "tienda/home.html", {"productos": productos})


def productos_catalogo(request):
    q = request.GET.get("q", "").strip()
    categoria_slug = request.GET.get("categoria", "").strip()
    categorias = Categoria.objects.filter(activa=True).order_by("orden", "nombre")
    productos_base = (
        Producto.objects.filter(activo=True, categoria__activa=True)
        .select_related("categoria")
        .prefetch_related("imagenes")
        .order_by("categoria__orden", "categoria__nombre", "orden", "nombre")
    )

    categoria_seleccionada = None
    productos = productos_base
    if categoria_slug:
        categoria_seleccionada = categorias.filter(slug=categoria_slug).first()
        productos = productos.filter(categoria=categoria_seleccionada) if categoria_seleccionada else productos.none()
    if q:
        productos = productos.filter(nombre__icontains=q)

    mostrar_resultados = bool(q or categoria_slug)
    categorias_con_productos = []
    if not mostrar_resultados:
        categorias_con_productos = (
            categorias.filter(productos__activo=True)
            .distinct()
            .prefetch_related(
                Prefetch(
                    "productos",
                    queryset=Producto.objects.filter(activo=True, categoria__activa=True)
                    .select_related("categoria")
                    .prefetch_related("imagenes")
                    .order_by("orden", "nombre"),
                )
            )
        )

    return render(
        request,
        "tienda/productos_catalogo.html",
        {
            "categorias": categorias,
            "categorias_con_productos": categorias_con_productos,
            "productos": productos,
            "q": q,
            "categoria_slug": categoria_slug,
            "categoria_seleccionada": categoria_seleccionada,
            "mostrar_resultados": mostrar_resultados,
        },
    )


def producto_detalle(request, slug):
    producto = get_object_or_404(Producto.objects.select_related("categoria"), slug=slug, activo=True, categoria__activa=True)
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

            return redirect("cotizacion_exito", pk=solicitud.pk, token=solicitud_token(solicitud.pk))
    else:
        form = DynamicSolicitudForm(producto)

    return render(request, "tienda/producto_detalle.html", {"producto": producto, "campos": campos, "galeria": galeria, "form": form})


def cotizacion_exito(request, pk, token):
    validate_solicitud_token(pk, token)
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
    redirect_value = request.POST.get(REDIRECT_FIELD_NAME) or request.GET.get(REDIRECT_FIELD_NAME, "")
    if request.user.is_authenticated:
        if request.user.is_staff:
            return redirect("panel_dashboard")
        raise PermissionDenied
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        if not user.is_staff:
            form.add_error(None, "No tienes permisos para acceder al panel.")
        else:
            login(request, user)
            return redirect(safe_panel_redirect(request))
    return render(request, "tienda/panel/login.html", {"form": form, REDIRECT_FIELD_NAME: redirect_value})


@login_required(login_url="panel_login")
@require_POST
def panel_logout(request):
    logout(request)
    return redirect("panel_login")


@panel_staff_required
def panel_dashboard(request):
    total = Solicitud.objects.count()
    nuevas = Solicitud.objects.filter(estado=Solicitud.ESTADO_NUEVA).count()
    revision = Solicitud.objects.filter(estado__in=[Solicitud.ESTADO_REVISION, Solicitud.ESTADO_PENDIENTE_INFO]).count()
    productos = Producto.objects.count()
    recientes = Solicitud.objects.select_related("producto").order_by("-creado")[:8]
    por_estado = Solicitud.objects.values("estado").annotate(total=Count("id")).order_by("estado")
    return render(request, "tienda/panel/dashboard.html", {"total": total, "nuevas": nuevas, "revision": revision, "productos": productos, "recientes": recientes, "por_estado": por_estado})


@panel_staff_required
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


@panel_staff_required
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


@panel_staff_required
def productos_lista(request):
    productos = Producto.objects.annotate(num_campos=Count("campos", distinct=True), num_solicitudes=Count("solicitudes", distinct=True)).order_by("orden", "nombre")
    return render(request, "tienda/panel/productos.html", {"productos": productos})


@panel_staff_required
def campos_maestros_lista(request):
    campos = CampoMaestro.objects.annotate(
        usos=Count("producto_campos", distinct=True),
        num_opciones=Count("opciones_maestras", distinct=True),
    ).order_by("orden_base", "nombre")
    return render(request, "tienda/panel/campos_maestros.html", {"campos": campos})


@panel_staff_required
def campo_maestro_crear(request):
    form = CampoMaestroForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Campo maestro creado.")
        return redirect("panel_campos_maestros")
    return render(request, "tienda/panel/campo_maestro_form.html", {"form": form, "titulo": "Crear campo maestro"})


@panel_staff_required
def campo_maestro_editar(request, maestro_id):
    campo = get_object_or_404(CampoMaestro, pk=maestro_id)
    form = CampoMaestroForm(request.POST or None, instance=campo)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Campo maestro actualizado.")
        return redirect("panel_campos_maestros")
    return render(request, "tienda/panel/campo_maestro_form.html", {"form": form, "campo": campo, "titulo": "Editar campo maestro"})


@panel_staff_required
def campo_maestro_opciones(request, maestro_id):
    campo = get_object_or_404(CampoMaestro, pk=maestro_id)
    opciones = campo.opciones_maestras.order_by("orden", "id")
    return render(request, "tienda/panel/campo_maestro_opciones.html", {"campo": campo, "opciones": opciones})


@panel_staff_required
def campo_maestro_opcion_crear(request, maestro_id):
    campo = get_object_or_404(CampoMaestro, pk=maestro_id)
    form = CampoMaestroOpcionForm(request.POST or None, campo_maestro=campo)
    if request.method == "POST" and form.is_valid():
        opcion = form.save(commit=False)
        opcion.campo_maestro = campo
        opcion.save()
        messages.success(request, "Opción maestra creada.")
        return redirect("panel_campo_maestro_opciones", maestro_id=campo.id)
    return render(request, "tienda/panel/campo_maestro_opcion_form.html", {"form": form, "campo": campo, "titulo": "Crear opción maestra"})


@panel_staff_required
def campo_maestro_opcion_editar(request, opcion_id):
    opcion = get_object_or_404(CampoMaestroOpcion.objects.select_related("campo_maestro"), pk=opcion_id)
    form = CampoMaestroOpcionForm(request.POST or None, instance=opcion)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Opción maestra actualizada.")
        return redirect("panel_campo_maestro_opciones", maestro_id=opcion.campo_maestro.id)
    return render(request, "tienda/panel/campo_maestro_opcion_form.html", {"form": form, "campo": opcion.campo_maestro, "opcion": opcion, "titulo": "Editar opción maestra"})


@panel_staff_required
def producto_crear(request):
    form = ProductoForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        producto = form.save()
        messages.success(request, "Producto creado. Ahora configura sus campos y opciones.")
        return redirect("panel_producto_campos", producto_id=producto.id)
    return render(request, "tienda/panel/producto_form.html", {"form": form, "titulo": "Crear producto"})


@panel_staff_required
def producto_editar(request, producto_id):
    producto = get_object_or_404(Producto, pk=producto_id)
    form = ProductoForm(request.POST or None, request.FILES or None, instance=producto)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Producto actualizado.")
        return redirect("panel_productos")
    return render(request, "tienda/panel/producto_form.html", {"form": form, "producto": producto, "titulo": "Editar producto"})


@panel_staff_required
def producto_campos(request, producto_id):
    producto = get_object_or_404(Producto, pk=producto_id)
    campos = producto.campos.select_related("campo_maestro").prefetch_related("opciones").order_by("orden", "id")
    maestros_asignados = producto.campos.filter(campo_maestro__isnull=False).values_list("campo_maestro_id", flat=True)
    maestros_disponibles = CampoMaestro.objects.filter(activo=True).exclude(pk__in=maestros_asignados).order_by("orden_base", "nombre")
    return render(
        request,
        "tienda/panel/producto_campos.html",
        {"producto": producto, "campos": campos, "maestros_disponibles": maestros_disponibles},
    )


@panel_staff_required
def campo_crear(request, producto_id):
    producto = get_object_or_404(Producto, pk=producto_id)
    form = ProductoCampoForm(request.POST or None, producto=producto)
    if request.method == "POST" and form.is_valid():
        campo = form.save(commit=False)
        campo.producto = producto
        campo.save()
        messages.success(request, "Campo creado.")
        return redirect("panel_producto_campos", producto_id=producto.id)
    return render(request, "tienda/panel/campo_form.html", {"form": form, "producto": producto, "titulo": "Crear campo"})


@panel_staff_required
@require_POST
def campo_asignar_maestro(request, producto_id, maestro_id):
    producto = get_object_or_404(Producto, pk=producto_id)
    maestro = get_object_or_404(CampoMaestro, pk=maestro_id, activo=True)
    campo_existente = ProductoCampo.objects.filter(producto=producto, campo_maestro=maestro).first()
    if campo_existente:
        if campo_existente.activo:
            messages.warning(request, "Ese campo maestro ya esta asignado a este producto.")
        else:
            campo_existente.activo = True
            try:
                campo_existente.save()
                creadas = campo_existente.copiar_opciones_maestras()
                detalle = f" Se copiaron {creadas} opciones faltantes." if creadas else ""
                messages.success(request, f"Campo reactivado para este producto.{detalle}")
            except ValidationError as exc:
                messages.error(request, validation_message(exc))
        return redirect("panel_producto_campos", producto_id=producto.id)

    campo = ProductoCampo.desde_maestro(producto, maestro)
    try:
        campo.save()
        creadas = campo.copiar_opciones_maestras()
        detalle = f" Se copiaron {creadas} opciones." if creadas else ""
        messages.success(request, f"Campo maestro asignado al producto.{detalle}")
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
    return redirect("panel_producto_campos", producto_id=producto.id)


@panel_staff_required
@require_POST
def campo_desactivar(request, campo_id):
    campo = get_object_or_404(ProductoCampo.objects.select_related("producto"), pk=campo_id)
    campo.activo = False
    campo.save()
    messages.success(request, "Campo quitado del formulario público de este producto.")
    return redirect("panel_producto_campos", producto_id=campo.producto.id)


@panel_staff_required
@require_POST
def campo_reactivar(request, campo_id):
    campo = get_object_or_404(ProductoCampo.objects.select_related("producto", "campo_maestro"), pk=campo_id)
    campo.activo = True
    try:
        campo.save()
        creadas = campo.copiar_opciones_maestras()
        detalle = f" Se copiaron {creadas} opciones faltantes." if creadas else ""
        messages.success(request, f"Campo reactivado.{detalle}")
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
    return redirect("panel_producto_campos", producto_id=campo.producto.id)


@panel_staff_required
@require_POST
def campo_copiar_opciones_maestro(request, campo_id):
    campo = get_object_or_404(ProductoCampo.objects.select_related("producto", "campo_maestro"), pk=campo_id)
    creadas = campo.copiar_opciones_maestras()
    if creadas:
        messages.success(request, f"Se copiaron {creadas} opciones maestras faltantes.")
    else:
        messages.info(request, "No había opciones maestras faltantes para copiar.")
    return redirect("panel_producto_campos", producto_id=campo.producto.id)


@panel_staff_required
def campo_editar(request, campo_id):
    campo = get_object_or_404(ProductoCampo.objects.select_related("producto"), pk=campo_id)
    form = ProductoCampoForm(request.POST or None, instance=campo)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Campo actualizado.")
        return redirect("panel_producto_campos", producto_id=campo.producto.id)
    return render(request, "tienda/panel/campo_form.html", {"form": form, "producto": campo.producto, "campo": campo, "titulo": "Editar campo"})


@panel_staff_required
def campo_opciones(request, campo_id):
    campo = get_object_or_404(ProductoCampo.objects.select_related("producto"), pk=campo_id)
    opciones = campo.opciones.order_by("orden", "id")
    return render(request, "tienda/panel/campo_opciones.html", {"campo": campo, "opciones": opciones})


@panel_staff_required
def opcion_crear(request, campo_id):
    campo = get_object_or_404(ProductoCampo.objects.select_related("producto"), pk=campo_id)
    form = CampoOpcionForm(request.POST or None, campo=campo)
    if request.method == "POST" and form.is_valid():
        opcion = form.save(commit=False)
        opcion.campo = campo
        opcion.save()
        messages.success(request, "Opción creada.")
        return redirect("panel_campo_opciones", campo_id=campo.id)
    return render(request, "tienda/panel/opcion_form.html", {"form": form, "campo": campo, "titulo": "Crear opción"})


@panel_staff_required
def opcion_editar(request, opcion_id):
    opcion = get_object_or_404(CampoOpcion.objects.select_related("campo__producto"), pk=opcion_id)
    form = CampoOpcionForm(request.POST or None, instance=opcion)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Opción actualizada.")
        return redirect("panel_campo_opciones", campo_id=opcion.campo.id)
    return render(request, "tienda/panel/opcion_form.html", {"form": form, "campo": opcion.campo, "opcion": opcion, "titulo": "Editar opción"})


@panel_staff_required
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


@panel_staff_required
@require_POST
def producto_toggle(request, producto_id):
    producto = get_object_or_404(Producto, pk=producto_id)
    producto.activo = not producto.activo
    producto.save(update_fields=["activo"])
    return redirect("panel_productos")


@panel_staff_required
@require_POST
def imagen_eliminar(request, imagen_id):
    imagen = get_object_or_404(ProductoImagen, pk=imagen_id)
    producto_id = imagen.producto_id
    imagen.delete()
    messages.success(request, "Imagen eliminada.")
    return redirect("panel_producto_imagenes", producto_id=producto_id)

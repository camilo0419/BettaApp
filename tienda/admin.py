from django.contrib import admin
from .models import (
    Categoria,
    CampoMaestro,
    CampoMaestroOpcion,
    CampoOpcion,
    Cliente,
    ClienteContacto,
    ClienteUsuario,
    Cotizacion,
    CotizacionItem,
    EmpleadoPerfil,
    Notificacion,
    NotificacionCliente,
    Producto,
    ProductoCampo,
    ProductoImagen,
    Proyecto,
    Solicitud,
    SolicitudAsignacion,
    SolicitudNovedad,
    SolicitudRespuesta,
    SolicitudTarea,
)

admin.site.site_header = "Admin Productos | Betta Diseño"
admin.site.site_title = "Admin Productos"
admin.site.index_title = "Gestión técnica"


class ProductoCampoInline(admin.TabularInline):
    model = ProductoCampo
    extra = 0


class ProductoImagenInline(admin.TabularInline):
    model = ProductoImagen
    extra = 0


class CampoMaestroOpcionInline(admin.TabularInline):
    model = CampoMaestroOpcion
    extra = 0


class ClienteContactoInline(admin.TabularInline):
    model = ClienteContacto
    extra = 0


class ClienteUsuarioInline(admin.TabularInline):
    model = ClienteUsuario
    extra = 0
    fields = [
        "user",
        "contacto",
        "activo",
        "puede_ver_proyectos",
        "puede_ver_solicitudes",
        "puede_ver_facturacion",
        "puede_descargar_archivos",
        "recibe_notificaciones",
    ]


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ["nombre", "activa", "orden", "slug"]
    list_filter = ["activa"]
    search_fields = ["nombre", "slug"]
    prepopulated_fields = {"slug": ("nombre",)}


@admin.register(CampoMaestro)
class CampoMaestroAdmin(admin.ModelAdmin):
    list_display = ["nombre", "tipo", "obligatorio_base", "activo", "orden_base", "slug", "actualizado"]
    list_filter = ["activo", "tipo"]
    search_fields = ["nombre", "slug", "etiqueta_base"]
    prepopulated_fields = {"slug": ("nombre",)}
    ordering = ["orden_base", "nombre"]
    inlines = [CampoMaestroOpcionInline]


@admin.register(CampoMaestroOpcion)
class CampoMaestroOpcionAdmin(admin.ModelAdmin):
    list_display = ["campo_maestro", "etiqueta", "valor", "ajuste_tipo", "precio", "orden", "activa"]
    list_filter = ["activa", "ajuste_tipo", "campo_maestro"]
    search_fields = ["etiqueta", "valor", "campo_maestro__nombre"]


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ["nombre", "razon_social", "tipo_cliente", "identificacion", "email", "telefono", "ciudad", "activo"]
    list_filter = ["tipo_cliente", "activo", "ciudad"]
    search_fields = ["nombre", "razon_social", "nombre_comercial", "identificacion", "email", "telefono", "whatsapp", "ciudad"]
    inlines = [ClienteContactoInline, ClienteUsuarioInline]


@admin.register(ClienteContacto)
class ClienteContactoAdmin(admin.ModelAdmin):
    list_display = ["cliente", "nombre", "cargo", "email", "telefono", "es_principal", "activo"]
    list_filter = ["activo", "es_principal"]
    search_fields = ["cliente__nombre", "cliente__razon_social", "nombre", "email", "telefono", "whatsapp"]


@admin.register(ClienteUsuario)
class ClienteUsuarioAdmin(admin.ModelAdmin):
    list_display = ["cliente", "user", "activo", "puede_ver_proyectos", "puede_ver_solicitudes", "puede_ver_facturacion", "fecha_ultimo_acceso"]
    list_filter = ["activo", "puede_ver_proyectos", "puede_ver_solicitudes", "puede_ver_facturacion", "recibe_notificaciones"]
    search_fields = ["cliente__nombre", "cliente__razon_social", "user__username", "user__email", "user__first_name", "user__last_name"]


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ["nombre", "categoria", "activo", "destacado", "orden", "actualizado"]
    list_filter = ["categoria", "activo", "destacado"]
    search_fields = ["nombre", "categoria__nombre", "slug"]
    prepopulated_fields = {"slug": ("nombre",)}
    inlines = [ProductoCampoInline, ProductoImagenInline]


@admin.register(ProductoCampo)
class ProductoCampoAdmin(admin.ModelAdmin):
    list_display = ["producto", "etiqueta", "campo_maestro", "tipo", "obligatorio", "orden", "activo"]
    list_filter = ["tipo", "obligatorio", "activo", "producto", "campo_maestro"]
    search_fields = ["etiqueta", "nombre_interno", "producto__nombre", "campo_maestro__nombre"]


@admin.register(CampoOpcion)
class CampoOpcionAdmin(admin.ModelAdmin):
    list_display = ["campo", "etiqueta", "ajuste_tipo", "precio", "orden", "activa"]
    list_filter = ["ajuste_tipo", "activa", "campo__producto"]
    search_fields = ["etiqueta", "campo__etiqueta", "campo__producto__nombre"]


class SolicitudRespuestaInline(admin.TabularInline):
    model = SolicitudRespuesta
    extra = 0


class SolicitudAsignacionInline(admin.TabularInline):
    model = SolicitudAsignacion
    extra = 0


class SolicitudTareaInline(admin.TabularInline):
    model = SolicitudTarea
    extra = 0


class SolicitudNovedadInline(admin.TabularInline):
    model = SolicitudNovedad
    extra = 0


class CotizacionItemInline(admin.TabularInline):
    model = CotizacionItem
    extra = 0
    readonly_fields = ["subtotal", "descuento_calculado", "impuesto_calculado", "total"]


@admin.register(Proyecto)
class ProyectoAdmin(admin.ModelAdmin):
    list_display = ["nombre", "cliente", "cliente_nombre", "estado", "prioridad", "responsable", "activo", "fecha_compromiso"]
    list_filter = ["estado", "prioridad", "activo", "cliente", "fecha_compromiso"]
    search_fields = ["nombre", "cliente__nombre", "cliente__razon_social", "cliente_nombre", "cliente_contacto", "responsable__user__username"]
    readonly_fields = ["fecha_creacion", "fecha_actualizacion"]


@admin.register(Solicitud)
class SolicitudAdmin(admin.ModelAdmin):
    list_display = ["id", "cliente_nombre", "cliente", "producto", "proyecto", "estado", "estado_produccion", "precio_estimado", "valor_facturado", "estado_facturacion", "creado"]
    list_filter = ["estado", "estado_produccion", "estado_facturacion", "cliente", "proyecto", "producto", "creado"]
    search_fields = ["cliente_nombre", "cliente_celular", "cliente__nombre", "cliente__razon_social", "producto__nombre", "proyecto__nombre"]
    inlines = [SolicitudRespuestaInline, SolicitudAsignacionInline, SolicitudTareaInline, SolicitudNovedadInline]


@admin.register(Cotizacion)
class CotizacionAdmin(admin.ModelAdmin):
    list_display = ["numero", "cliente", "proyecto", "solicitud", "estado", "subtotal", "descuento_total", "impuesto_total", "total", "fecha_creacion"]
    list_filter = ["estado", "moneda", "activa", "fecha_creacion", "cliente"]
    search_fields = ["numero", "titulo", "cliente__nombre", "cliente__razon_social", "proyecto__nombre"]
    readonly_fields = ["numero", "subtotal", "descuento_total", "impuesto_total", "total", "fecha_creacion", "fecha_actualizacion"]
    inlines = [CotizacionItemInline]


@admin.register(CotizacionItem)
class CotizacionItemAdmin(admin.ModelAdmin):
    list_display = ["cotizacion", "descripcion", "cantidad", "valor_unitario", "descuento_calculado", "impuesto_calculado", "total", "activo"]
    list_filter = ["activo", "producto"]
    search_fields = ["cotizacion__numero", "descripcion", "producto__nombre"]
    readonly_fields = ["subtotal", "descuento_calculado", "impuesto_calculado", "total"]


@admin.register(EmpleadoPerfil)
class EmpleadoPerfilAdmin(admin.ModelAdmin):
    list_display = ["user", "area", "cargo", "activo", "puede_recibir_pedidos", "actualizado"]
    list_filter = ["area", "activo", "puede_recibir_pedidos"]
    search_fields = ["user__username", "user__first_name", "user__last_name", "user__email", "cargo"]


@admin.register(SolicitudAsignacion)
class SolicitudAsignacionAdmin(admin.ModelAdmin):
    list_display = ["solicitud", "empleado", "rol_en_trabajo", "activa", "fecha_asignacion", "asignado_por"]
    list_filter = ["activa", "empleado__area", "fecha_asignacion"]
    search_fields = ["solicitud__cliente_nombre", "solicitud__producto__nombre", "empleado__user__username"]


@admin.register(SolicitudTarea)
class SolicitudTareaAdmin(admin.ModelAdmin):
    list_display = ["solicitud", "titulo", "responsable", "area", "estado", "prioridad", "fecha_limite", "activa"]
    list_filter = ["estado", "prioridad", "area", "activa", "fecha_limite"]
    search_fields = ["titulo", "descripcion", "solicitud__cliente_nombre", "solicitud__producto__nombre", "responsable__user__username"]


@admin.register(SolicitudNovedad)
class SolicitudNovedadAdmin(admin.ModelAdmin):
    list_display = ["solicitud", "tarea", "tipo", "usuario", "visible_para_cliente", "fecha_creacion"]
    list_filter = ["tipo", "visible_para_cliente", "fecha_creacion"]
    search_fields = ["solicitud__cliente_nombre", "comentario", "usuario__username"]


@admin.register(Notificacion)
class NotificacionAdmin(admin.ModelAdmin):
    list_display = ["usuario_destino", "titulo", "tipo", "leida", "fecha_creacion"]
    list_filter = ["tipo", "leida", "fecha_creacion"]
    search_fields = ["usuario_destino__username", "titulo", "mensaje"]


@admin.register(NotificacionCliente)
class NotificacionClienteAdmin(admin.ModelAdmin):
    list_display = ["cliente", "cliente_usuario", "titulo", "tipo", "leida", "fecha_creacion"]
    list_filter = ["tipo", "leida", "fecha_creacion"]
    search_fields = ["cliente__nombre", "cliente__razon_social", "cliente_usuario__user__email", "titulo", "mensaje"]


admin.site.register(ProductoImagen)

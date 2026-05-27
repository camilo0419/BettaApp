from django.contrib import admin
from .models import CampoOpcion, Producto, ProductoCampo, ProductoImagen, Solicitud, SolicitudRespuesta

admin.site.site_header = "Admin Productos | Betta Diseño"
admin.site.site_title = "Admin Productos"
admin.site.index_title = "Gestión técnica"


class ProductoCampoInline(admin.TabularInline):
    model = ProductoCampo
    extra = 0


class ProductoImagenInline(admin.TabularInline):
    model = ProductoImagen
    extra = 0


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ["nombre", "categoria", "tipo_calculo", "activo", "orden"]
    list_filter = ["activo", "tipo_calculo", "categoria"]
    search_fields = ["nombre", "categoria", "slug"]
    prepopulated_fields = {"slug": ("nombre",)}
    inlines = [ProductoCampoInline, ProductoImagenInline]


@admin.register(ProductoCampo)
class ProductoCampoAdmin(admin.ModelAdmin):
    list_display = ["producto", "etiqueta", "tipo", "obligatorio", "orden", "activo"]
    list_filter = ["tipo", "obligatorio", "activo", "producto"]
    search_fields = ["etiqueta", "nombre_interno", "producto__nombre"]


@admin.register(CampoOpcion)
class CampoOpcionAdmin(admin.ModelAdmin):
    list_display = ["campo", "etiqueta", "ajuste_tipo", "precio", "orden", "activa"]
    list_filter = ["ajuste_tipo", "activa", "campo__producto"]
    search_fields = ["etiqueta", "campo__etiqueta", "campo__producto__nombre"]


class SolicitudRespuestaInline(admin.TabularInline):
    model = SolicitudRespuesta
    extra = 0


@admin.register(Solicitud)
class SolicitudAdmin(admin.ModelAdmin):
    list_display = ["id", "cliente_nombre", "producto", "estado", "precio_estimado", "creado"]
    list_filter = ["estado", "producto", "creado"]
    search_fields = ["cliente_nombre", "cliente_celular", "producto__nombre"]
    inlines = [SolicitudRespuestaInline]


admin.site.register(ProductoImagen)

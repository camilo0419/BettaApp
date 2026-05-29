from django.contrib import admin
from .models import Categoria, CampoMaestro, CampoMaestroOpcion, CampoOpcion, Producto, ProductoCampo, ProductoImagen, Solicitud, SolicitudRespuesta

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


@admin.register(Solicitud)
class SolicitudAdmin(admin.ModelAdmin):
    list_display = ["id", "cliente_nombre", "producto", "estado", "precio_estimado", "creado"]
    list_filter = ["estado", "producto", "creado"]
    search_fields = ["cliente_nombre", "cliente_celular", "producto__nombre"]
    inlines = [SolicitudRespuestaInline]


admin.site.register(ProductoImagen)

from django.contrib import admin
from .models import Cotizacion, OpcionProducto, Producto


class OpcionInline(admin.TabularInline):
    model = OpcionProducto
    extra = 1


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'categoria', 'tipo_calculo', 'precio_base', 'activo', 'orden')
    list_filter = ('activo', 'tipo_calculo', 'categoria')
    search_fields = ('nombre', 'categoria')
    prepopulated_fields = {'slug': ('nombre',)}
    inlines = [OpcionInline]


@admin.register(OpcionProducto)
class OpcionProductoAdmin(admin.ModelAdmin):
    list_display = ('producto', 'tipo', 'nombre', 'valor_extra', 'multiplica_area', 'activo', 'orden')
    list_filter = ('tipo', 'activo', 'multiplica_area')
    search_fields = ('producto__nombre', 'nombre')


@admin.register(Cotizacion)
class CotizacionAdmin(admin.ModelAdmin):
    list_display = ('id', 'producto', 'nombre_cliente', 'celular', 'precio_estimado', 'requiere_revision', 'estado', 'creado')
    list_filter = ('estado', 'producto', 'requiere_revision')
    search_fields = ('nombre_cliente', 'celular', 'email', 'observaciones', 'notas_internas')
    readonly_fields = ('creado', 'actualizado', 'opciones_json')

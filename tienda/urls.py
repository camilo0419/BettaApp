from django.urls import path
from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("productos/", views.productos_catalogo, name="productos_catalogo"),
    path("productos/<slug:slug>/", views.producto_detalle, name="producto_detalle"),
    path("solicitud/<int:pk>/exito/<str:token>/", views.cotizacion_exito, name="cotizacion_exito"),

    path("panel/login/", views.panel_login, name="panel_login"),
    path("panel/logout/", views.panel_logout, name="panel_logout"),
    path("panel/", views.panel_dashboard, name="panel_dashboard"),
    path("panel/solicitudes/", views.solicitudes_lista, name="panel_solicitudes"),
    path("panel/solicitudes/<int:pk>/", views.solicitud_detalle, name="panel_solicitud_detalle"),

    path("panel/campos-maestros/", views.campos_maestros_lista, name="panel_campos_maestros"),
    path("panel/campos-maestros/nuevo/", views.campo_maestro_crear, name="panel_campo_maestro_crear"),
    path("panel/campos-maestros/<int:maestro_id>/editar/", views.campo_maestro_editar, name="panel_campo_maestro_editar"),
    path("panel/campos-maestros/<int:maestro_id>/opciones/", views.campo_maestro_opciones, name="panel_campo_maestro_opciones"),
    path("panel/campos-maestros/<int:maestro_id>/opciones/nueva/", views.campo_maestro_opcion_crear, name="panel_campo_maestro_opcion_crear"),
    path("panel/campos-maestros/opciones/<int:opcion_id>/editar/", views.campo_maestro_opcion_editar, name="panel_campo_maestro_opcion_editar"),

    path("panel/productos/", views.productos_lista, name="panel_productos"),
    path("panel/productos/nuevo/", views.producto_crear, name="panel_producto_crear"),
    path("panel/productos/<int:producto_id>/editar/", views.producto_editar, name="panel_producto_editar"),
    path("panel/productos/<int:producto_id>/toggle/", views.producto_toggle, name="panel_producto_toggle"),
    path("panel/productos/<int:producto_id>/campos/", views.producto_campos, name="panel_producto_campos"),
    path("panel/productos/<int:producto_id>/campos/nuevo/", views.campo_crear, name="panel_campo_crear"),
    path("panel/productos/<int:producto_id>/campos/asignar/<int:maestro_id>/", views.campo_asignar_maestro, name="panel_campo_asignar_maestro"),
    path("panel/campos/<int:campo_id>/desactivar/", views.campo_desactivar, name="panel_campo_desactivar"),
    path("panel/campos/<int:campo_id>/reactivar/", views.campo_reactivar, name="panel_campo_reactivar"),
    path("panel/campos/<int:campo_id>/copiar-opciones/", views.campo_copiar_opciones_maestro, name="panel_campo_copiar_opciones_maestro"),
    path("panel/campos/<int:campo_id>/editar/", views.campo_editar, name="panel_campo_editar"),
    path("panel/campos/<int:campo_id>/opciones/", views.campo_opciones, name="panel_campo_opciones"),
    path("panel/campos/<int:campo_id>/opciones/nueva/", views.opcion_crear, name="panel_opcion_crear"),
    path("panel/opciones/<int:opcion_id>/editar/", views.opcion_editar, name="panel_opcion_editar"),
    path("panel/productos/<int:producto_id>/imagenes/", views.producto_imagenes, name="panel_producto_imagenes"),
    path("panel/imagenes/<int:imagen_id>/eliminar/", views.imagen_eliminar, name="panel_imagen_eliminar"),
]

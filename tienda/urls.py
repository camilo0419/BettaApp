from django.contrib.auth.views import LogoutView
from django.urls import path
from . import views

app_name = 'tienda'

urlpatterns = [
    path('', views.home, name='home'),
    path('producto/<slug:slug>/', views.producto_detalle, name='producto_detalle'),
    path('cotizacion/<int:pk>/exito/', views.cotizacion_exito, name='cotizacion_exito'),

    path('panel/login/', views.PanelLoginView.as_view(), name='panel_login'),
    path('panel/logout/', LogoutView.as_view(), name='panel_logout'),
    path('panel/', views.panel_dashboard, name='panel_dashboard'),
    path('panel/solicitudes/', views.panel_cotizaciones, name='panel_cotizaciones'),
    path('panel/solicitudes/<int:pk>/', views.panel_cotizacion_detalle, name='panel_cotizacion_detalle'),
    path('panel/solicitudes/<int:pk>/editar/', views.panel_cotizacion_editar, name='panel_cotizacion_editar'),
]

from decimal import Decimal
from django.core.management.base import BaseCommand
from tienda.models import OpcionProducto, Producto


PRODUCTOS = [
    {
        'nombre': 'Vinilo adhesivo personalizado',
        'slug': 'vinilo-adhesivo-personalizado',
        'categoria': 'Vinilos',
        'descripcion_corta': 'Vinilo para vitrinas, paredes, vehículos, etiquetas o señalización interior y exterior.',
        'imagen': 'tienda/img/vinilo.svg',
        'tipo_calculo': 'area_m2',
        'precio_base': Decimal('60000'),
        'unidad_precio': 'm²',
        'minimo_facturable': Decimal('0.50'),
        'requiere_revision': False,
        'orden': 1,
        'opciones': [
            ('material', 'Vinilo blanco', 0, True),
            ('material', 'Vinilo transparente', 8000, True),
            ('material', 'Vinilo microperforado', 25000, True),
            ('material', 'Vinilo ácido', 18000, True),
            ('acabado', 'Sin laminado', 0, True),
            ('acabado', 'Laminado mate', 12000, True),
            ('acabado', 'Laminado brillante', 12000, True),
            ('extra', 'Corte especial', 15000, False),
            ('extra', 'Instalación en Medellín', 45000, False),
        ],
    },
    {
        'nombre': 'Lona / banner publicitario',
        'slug': 'lona-banner-publicitario',
        'categoria': 'Gran formato',
        'descripcion_corta': 'Banner en lona para fachadas, eventos, promociones, cierres de obra o puntos de venta.',
        'imagen': 'tienda/img/lona.svg',
        'tipo_calculo': 'area_m2',
        'precio_base': Decimal('45000'),
        'unidad_precio': 'm²',
        'minimo_facturable': Decimal('1.00'),
        'requiere_revision': False,
        'orden': 2,
        'opciones': [
            ('material', 'Lona 13 oz', 0, True),
            ('material', 'Lona 15 oz', 10000, True),
            ('acabado', 'Refilado sencillo', 0, True),
            ('acabado', 'Dobladillo y ojaletes', 18000, True),
            ('extra', 'Diseño para promoción', 35000, False),
            ('extra', 'Entrega urgente', 30000, False),
        ],
    },
    {
        'nombre': 'Pendón tipo roll up',
        'slug': 'pendon-tipo-roll-up',
        'categoria': 'Eventos',
        'descripcion_corta': 'Pendón portátil para ferias, eventos, activaciones comerciales y puntos informativos.',
        'imagen': 'tienda/img/pendon.svg',
        'tipo_calculo': 'unidad',
        'precio_base': Decimal('185000'),
        'unidad_precio': 'unidad',
        'minimo_facturable': Decimal('1.00'),
        'requiere_revision': False,
        'orden': 3,
        'opciones': [
            ('material', 'Estructura estándar 80x200 cm', 0, False),
            ('material', 'Estructura premium 85x200 cm', 55000, False),
            ('acabado', 'Impresión mate', 0, False),
            ('acabado', 'Impresión brillante', 12000, False),
            ('extra', 'Bolso de transporte', 18000, False),
            ('extra', 'Diseño básico', 35000, False),
        ],
    },
    {
        'nombre': 'Acrílico / metacrilato personalizado',
        'slug': 'acrilico-metacrilato-personalizado',
        'categoria': 'Señalética',
        'descripcion_corta': 'Placas acrílicas para señalización, avisos interiores, logos, directorios y piezas decorativas.',
        'imagen': 'tienda/img/acrilico.svg',
        'tipo_calculo': 'area_m2',
        'precio_base': Decimal('150000'),
        'unidad_precio': 'm²',
        'minimo_facturable': Decimal('0.30'),
        'requiere_revision': True,
        'orden': 4,
        'opciones': [
            ('material', 'Acrílico transparente 3 mm', 0, True),
            ('material', 'Acrílico transparente 5 mm', 45000, True),
            ('material', 'Acrílico blanco', 30000, True),
            ('acabado', 'Corte recto', 0, True),
            ('acabado', 'Cantos pulidos', 35000, True),
            ('extra', 'Separadores metálicos', 25000, False),
            ('extra', 'Instalación básica', 60000, False),
        ],
    },
    {
        'nombre': 'Letras corpóreas',
        'slug': 'letras-corporeas',
        'categoria': 'Avisos',
        'descripcion_corta': 'Letras en PVC, acrílico o MDF para avisos interiores, fachadas y decoración de marca.',
        'imagen': 'tienda/img/letras.svg',
        'tipo_calculo': 'estimado',
        'precio_base': Decimal('250000'),
        'unidad_precio': 'proyecto',
        'minimo_facturable': Decimal('1.00'),
        'requiere_revision': True,
        'orden': 5,
        'opciones': [
            ('material', 'PVC 10 mm', 0, False),
            ('material', 'PVC 15 mm', 80000, False),
            ('material', 'Acrílico', 120000, False),
            ('material', 'MDF pintado', 65000, False),
            ('acabado', 'Pintura básica', 0, False),
            ('acabado', 'Pintura premium', 70000, False),
            ('iluminacion', 'Sin iluminación', 0, False),
            ('iluminacion', 'Retroiluminada', 220000, False),
            ('iluminacion', 'Frontal luminosa', 300000, False),
            ('extra', 'Instalación en fachada', 120000, False),
        ],
    },
]


class Command(BaseCommand):
    help = 'Crea los 5 productos iniciales del MVP con sus opciones.'

    def handle(self, *args, **options):
        for data in PRODUCTOS:
            opciones = data.pop('opciones')
            producto, _ = Producto.objects.update_or_create(
                slug=data['slug'], defaults=data
            )
            for index, (tipo, nombre, valor, multiplica_area) in enumerate(opciones, start=1):
                OpcionProducto.objects.update_or_create(
                    producto=producto,
                    tipo=tipo,
                    nombre=nombre,
                    defaults={
                        'valor_extra': Decimal(str(valor)),
                        'multiplica_area': multiplica_area,
                        'activo': True,
                        'orden': index,
                    }
                )
        self.stdout.write(self.style.SUCCESS('Productos iniciales creados/actualizados.'))

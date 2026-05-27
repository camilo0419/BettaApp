from decimal import Decimal
from django.core.management.base import BaseCommand
from tienda.models import CampoOpcion, Producto, ProductoCampo


def upsert_field(producto, etiqueta, tipo, orden, **kwargs):
    campo, _ = ProductoCampo.objects.update_or_create(
        producto=producto,
        etiqueta=etiqueta,
        defaults={"tipo": tipo, "orden": orden, **kwargs},
    )
    return campo


def upsert_option(campo, etiqueta, orden, ajuste_tipo="ninguno", precio=0):
    CampoOpcion.objects.update_or_create(
        campo=campo,
        etiqueta=etiqueta,
        defaults={"orden": orden, "ajuste_tipo": ajuste_tipo, "precio": Decimal(str(precio)), "activa": True},
    )


class Command(BaseCommand):
    help = "Carga productos configurables de ejemplo para Betta Diseño. Es seguro ejecutarlo varias veces."

    def handle(self, *args, **options):
        data = [
            {
                "nombre": "Vinilo adhesivo personalizado",
                "slug": "vinilo-adhesivo-personalizado",
                "categoria": "Vinilos",
                "descripcion_corta": "Vinilos para vitrinas, muros, vehículos, branding interior o exterior.",
                "descripcion_larga": "Configura medidas, tipo de vinilo, acabado, diseño y extras. Precio estimado por metro cuadrado.",
                "imagen_estatica": "tienda/img/vinilo.svg",
                "orden": 1,
                "tipo_calculo": Producto.CALCULO_AREA,
                "precio_base_m2": 60000,
            },
            {
                "nombre": "Lona / banner publicitario",
                "slug": "lona-banner-publicitario",
                "categoria": "Gran formato",
                "descripcion_corta": "Lonas para fachadas, eventos, promociones y puntos de venta.",
                "descripcion_larga": "Producto ideal para exteriores e interiores. Puedes configurar ojales, refuerzos, bolsillo para tubo y acabados.",
                "imagen_estatica": "tienda/img/lona.svg",
                "orden": 2,
                "tipo_calculo": Producto.CALCULO_AREA,
                "precio_base_m2": 45000,
            },
            {
                "nombre": "Pendón tipo roll up",
                "slug": "pendon-roll-up",
                "categoria": "Exhibición",
                "descripcion_corta": "Pendón portátil para eventos, ferias, puntos de venta y activaciones.",
                "descripcion_larga": "Configura medida, base, cantidad, diseño y fecha requerida. Precio estimado por unidad.",
                "imagen_estatica": "tienda/img/pendon.svg",
                "orden": 3,
                "tipo_calculo": Producto.CALCULO_UNIDAD,
                "precio_base_unidad": 180000,
            },
            {
                "nombre": "Acrílico / metacrilato personalizado",
                "slug": "acrilico-metacrilato-personalizado",
                "categoria": "Acrílicos",
                "descripcion_corta": "Piezas en acrílico para señalética, placas, exhibidores y decoración.",
                "descripcion_larga": "Permite configurar medidas, grosor, forma, cantos, impresión y sistema de instalación.",
                "imagen_estatica": "tienda/img/acrilico.svg",
                "orden": 4,
                "tipo_calculo": Producto.CALCULO_AREA,
                "precio_base_m2": 160000,
            },
            {
                "nombre": "Letras corpóreas",
                "slug": "letras-corporeas",
                "categoria": "Avisos",
                "descripcion_corta": "Letras en acrílico, PVC, MDF o metal para marcas y fachadas.",
                "descripcion_larga": "Producto de alta personalización. El precio final requiere revisión según texto, material, tamaño e instalación.",
                "imagen_estatica": "tienda/img/letras.svg",
                "orden": 5,
                "tipo_calculo": Producto.CALCULO_MANUAL,
                "requiere_revision": True,
            },
        ]

        for item in data:
            producto, _ = Producto.objects.update_or_create(
                slug=item["slug"],
                defaults={**item, "activo": True, "destacado": True},
            )
            self._campos_base(producto)

        self.stdout.write(self.style.SUCCESS("Productos configurables de ejemplo listos."))

    def _campos_base(self, producto):
        is_manual = producto.tipo_calculo == Producto.CALCULO_MANUAL
        texto = upsert_field(producto, "Texto / referencia", ProductoCampo.TIPO_TEXTO, 1, obligatorio=False, placeholder="Ej: Restaurante La 80 / Promo / Nombre del aviso")
        color = upsert_field(producto, "Color principal", ProductoCampo.TIPO_COLOR, 2, obligatorio=False)
        if not is_manual:
            upsert_field(producto, "Ancho en cm", ProductoCampo.TIPO_NUMERO, 3, obligatorio=True, afecta_area_ancho=True, placeholder="Ej: 120")
            upsert_field(producto, "Alto en cm", ProductoCampo.TIPO_NUMERO, 4, obligatorio=True, afecta_area_alto=True, placeholder="Ej: 80")
        else:
            upsert_field(producto, "Alto aproximado de letra en cm", ProductoCampo.TIPO_NUMERO, 3, obligatorio=True, placeholder="Ej: 30")
            upsert_field(producto, "Ancho total aproximado en cm", ProductoCampo.TIPO_NUMERO, 4, obligatorio=False, placeholder="Ej: 180")
        upsert_field(producto, "Cantidad", ProductoCampo.TIPO_ENTERO, 5, obligatorio=True, es_cantidad=True, placeholder="Ej: 1")
        diseno = upsert_field(producto, "Estado del diseño", ProductoCampo.TIPO_SELECT, 6, obligatorio=True)
        upsert_option(diseno, "Tengo mi diseño", 1)
        upsert_option(diseno, "Necesito que me diseñen", 2, "fijo", 35000)
        upsert_option(diseno, "Quiero asesoría antes de producir", 3)

        if "Vinilo" in producto.nombre:
            material = upsert_field(producto, "Material", ProductoCampo.TIPO_SELECT, 7, obligatorio=True)
            for i, (label, price) in enumerate([("Vinilo blanco", 0), ("Vinilo transparente", 15000), ("Microperforado", 25000), ("Vinilo ácido", 20000), ("Reflectivo", 55000)], 1):
                upsert_option(material, label, i, "por_m2" if price else "ninguno", price)
            acabado = upsert_field(producto, "Acabado", ProductoCampo.TIPO_SELECT, 8, obligatorio=True)
            upsert_option(acabado, "Sin laminado", 1)
            upsert_option(acabado, "Laminado mate", 2, "por_m2", 12000)
            upsert_option(acabado, "Laminado brillante", 3, "por_m2", 12000)
        elif "Lona" in producto.nombre:
            material = upsert_field(producto, "Material", ProductoCampo.TIPO_SELECT, 7, obligatorio=True)
            upsert_option(material, "Lona 13 oz", 1)
            upsert_option(material, "Lona reforzada", 2, "por_m2", 12000)
            extras = upsert_field(producto, "Extras", ProductoCampo.TIPO_MULTISELECT, 8, obligatorio=False)
            upsert_option(extras, "Ojales", 1, "fijo", 10000)
            upsert_option(extras, "Refuerzo perimetral", 2, "por_m2", 8000)
            upsert_option(extras, "Bolsillo para tubo", 3, "fijo", 12000)
        elif "Pendón" in producto.nombre:
            base = upsert_field(producto, "Tipo de base", ProductoCampo.TIPO_SELECT, 7, obligatorio=True)
            upsert_option(base, "Roll up estándar", 1)
            upsert_option(base, "Roll up reforzado", 2, "por_unidad", 50000)
            tam = upsert_field(producto, "Tamaño", ProductoCampo.TIPO_SELECT, 8, obligatorio=True)
            upsert_option(tam, "80 x 200 cm", 1)
            upsert_option(tam, "100 x 200 cm", 2, "por_unidad", 35000)
        elif "Acrílico" in producto.nombre:
            grosor = upsert_field(producto, "Grosor", ProductoCampo.TIPO_SELECT, 7, obligatorio=True)
            upsert_option(grosor, "3 mm", 1)
            upsert_option(grosor, "5 mm", 2, "por_m2", 30000)
            upsert_option(grosor, "8 mm", 3, "por_m2", 70000)
            acabado = upsert_field(producto, "Acabado", ProductoCampo.TIPO_SELECT, 8, obligatorio=True)
            upsert_option(acabado, "Transparente", 1)
            upsert_option(acabado, "Impresión directa", 2, "por_m2", 45000)
            upsert_option(acabado, "Cantos pulidos", 3, "por_m2", 25000)
        elif "Letras" in producto.nombre:
            mat = upsert_field(producto, "Material", ProductoCampo.TIPO_SELECT, 7, obligatorio=True)
            for i, label in enumerate(["PVC", "Acrílico", "MDF", "Acero", "Aluminio"], 1):
                upsert_option(mat, label, i)
            ilum = upsert_field(producto, "Iluminación", ProductoCampo.TIPO_SELECT, 8, obligatorio=True)
            for i, label in enumerate(["Sin iluminación", "Frontal", "Retroiluminada", "RGB"], 1):
                upsert_option(ilum, label, i)

        upsert_field(producto, "Archivo de diseño o logo", ProductoCampo.TIPO_ARCHIVO, 20, obligatorio=False, ayuda="Puedes subir PDF, JPG, PNG, SVG, AI o archivo de referencia.")
        upsert_field(producto, "Observaciones", ProductoCampo.TIPO_TEXTO_LARGO, 21, obligatorio=False, placeholder="Cuéntanos ubicación, uso, fecha requerida o detalles importantes.")

from io import BytesIO
import re
import unicodedata
from xml.sax.saxutils import escape

from django.conf import settings
from django.utils import timezone


def formato_moneda(valor, moneda="COP"):
    texto = f"${valor:,.0f}".replace(",", ".")
    return f"{texto} {moneda}".strip()


def nombre_archivo_cotizacion(cotizacion):
    numero = cotizacion.numero or f"COT-{cotizacion.pk:06d}"
    base = f"Cotizacion-{numero}-Betta-Diseno.pdf"
    normalized = unicodedata.normalize("NFKD", base)
    ascii_name = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", ascii_name).strip("-")


def pdf_text(value):
    return escape(str(value or ""))


def generar_pdf_cotizacion(cotizacion):
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_RIGHT
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise RuntimeError("ReportLab no esta instalado; instala requirements.txt para generar PDFs adjuntos.") from exc

    cotizacion.recalcular_totales()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.4 * cm,
        bottomMargin=1.4 * cm,
        title=f"Cotizacion {cotizacion.numero}",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Right", parent=styles["Normal"], alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name="SmallMuted", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#5f747b")))
    styles.add(ParagraphStyle(name="SectionTitle", parent=styles["Heading2"], fontSize=13, textColor=colors.HexColor("#034860"), spaceAfter=8))

    story = []
    marca = getattr(settings, "BETTA_EMAIL_FROM_NAME", "Betta Diseno") or "Betta Diseno"
    story.append(Paragraph(f"<b>{pdf_text(marca)}</b>", styles["Title"]))
    story.append(Paragraph("Cotizacion comercial", styles["SmallMuted"]))
    story.append(Spacer(1, 10))

    cliente = cotizacion.cliente
    contacto = cotizacion.contacto
    destinatario = contacto.nombre if contacto else (cliente.contacto_principal or str(cliente))
    email = (contacto.email if contacto else "") or cliente.email or "-"
    telefono = cliente.telefono or cliente.whatsapp or "-"
    fecha_emision = cotizacion.fecha_emision or timezone.localdate()
    vencimiento = cotizacion.fecha_vencimiento or ""

    resumen = Table(
        [
            [Paragraph("<b>Cliente</b>", styles["Normal"]), Paragraph(pdf_text(cliente), styles["Normal"]), Paragraph("<b>Cotizacion</b>", styles["Normal"]), Paragraph(pdf_text(cotizacion.numero), styles["Right"])],
            [Paragraph("<b>Contacto</b>", styles["Normal"]), Paragraph(pdf_text(destinatario or "-"), styles["Normal"]), Paragraph("<b>Emision</b>", styles["Normal"]), Paragraph(fecha_emision.strftime("%d/%m/%Y"), styles["Right"])],
            [Paragraph("<b>Email</b>", styles["Normal"]), Paragraph(pdf_text(email), styles["Normal"]), Paragraph("<b>Vencimiento</b>", styles["Normal"]), Paragraph(vencimiento.strftime("%d/%m/%Y") if vencimiento else "-", styles["Right"])],
            [Paragraph("<b>Telefono</b>", styles["Normal"]), Paragraph(pdf_text(telefono), styles["Normal"]), Paragraph("<b>Moneda</b>", styles["Normal"]), Paragraph(pdf_text(cotizacion.moneda), styles["Right"])],
        ],
        colWidths=[2.7 * cm, 7.3 * cm, 3 * cm, 4 * cm],
    )
    resumen.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dce8e8")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dce8e8")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8f8f6")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(resumen)
    story.append(Spacer(1, 12))

    story.append(Paragraph(pdf_text(cotizacion.titulo), styles["SectionTitle"]))
    if cotizacion.descripcion:
        story.append(Paragraph(pdf_text(cotizacion.descripcion).replace("\n", "<br/>"), styles["Normal"]))
        story.append(Spacer(1, 8))

    item_rows = [[
        Paragraph("<b>Descripcion</b>", styles["Normal"]),
        Paragraph("<b>Cant.</b>", styles["Normal"]),
        Paragraph("<b>Unitario</b>", styles["Right"]),
        Paragraph("<b>Desc.</b>", styles["Right"]),
        Paragraph("<b>Imp.</b>", styles["Right"]),
        Paragraph("<b>Total</b>", styles["Right"]),
    ]]
    for item in cotizacion.items.filter(activo=True).select_related("producto").order_by("orden", "id"):
        detalle = f"<b>{pdf_text(item.descripcion)}</b>"
        if item.detalle:
            detalle = f"{detalle}<br/><font size='8'>{pdf_text(item.detalle).replace(chr(10), '<br/>')}</font>"
        item_rows.append(
            [
                Paragraph(detalle, styles["Normal"]),
                Paragraph(f"{item.cantidad:g} {item.unidad}", styles["Normal"]),
                Paragraph(formato_moneda(item.valor_unitario, ""), styles["Right"]),
                Paragraph(formato_moneda(item.descuento_calculado, ""), styles["Right"]),
                Paragraph(formato_moneda(item.impuesto_calculado, ""), styles["Right"]),
                Paragraph(formato_moneda(item.total, ""), styles["Right"]),
            ]
        )
    table = Table(item_rows, colWidths=[6.2 * cm, 1.6 * cm, 2.6 * cm, 2.2 * cm, 2.1 * cm, 2.7 * cm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#034860")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dce8e8")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 12))

    totales = Table(
        [
            ["Subtotal", formato_moneda(cotizacion.subtotal, cotizacion.moneda)],
            ["Descuento", formato_moneda(cotizacion.descuento_total, cotizacion.moneda)],
            ["Impuestos", formato_moneda(cotizacion.impuesto_total, cotizacion.moneda)],
            ["Total", formato_moneda(cotizacion.total, cotizacion.moneda)],
        ],
        colWidths=[4 * cm, 4 * cm],
        hAlign="RIGHT",
    )
    totales.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dce8e8")),
                ("BACKGROUND", (0, 3), (-1, 3), colors.HexColor("#e8f8f6")),
                ("FONTNAME", (0, 3), (-1, 3), "Helvetica-Bold"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("PADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(totales)
    story.append(Spacer(1, 12))

    condiciones = [
        f"Validez: {cotizacion.validez_dias} dias",
        f"Tiempo de entrega: {cotizacion.tiempo_entrega or 'Por confirmar'}",
        f"Forma de pago: {cotizacion.forma_pago or 'Por confirmar'}",
        f"Garantia: {cotizacion.garantia or 'Segun condiciones del producto'}",
    ]
    story.append(Paragraph("<br/>".join(condiciones), styles["Normal"]))
    if cotizacion.observaciones_cliente:
        story.append(Spacer(1, 8))
        story.append(Paragraph(pdf_text(cotizacion.observaciones_cliente).replace("\n", "<br/>"), styles["Normal"]))
    if cotizacion.condiciones_comerciales:
        story.append(Spacer(1, 8))
        story.append(Paragraph(pdf_text(cotizacion.condiciones_comerciales).replace("\n", "<br/>"), styles["SmallMuted"]))

    doc.build(story)
    return buffer.getvalue()

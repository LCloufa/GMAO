from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _money(value):
    try:
        return f"{float(value or 0):,.2f} €".replace(",", " ").replace(".", ",")
    except (TypeError, ValueError):
        return "0,00 €"


def create_purchase_order_pdf(order, lines, totals):
    """Construit un bon de commande fournisseur PDF en mémoire."""
    output = BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Bon de commande {order[1]}",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=8.5, leading=11))
    styles.add(ParagraphStyle(name="Right", parent=styles["BodyText"], alignment=TA_RIGHT, fontSize=9))
    styles.add(ParagraphStyle(name="TitleLeft", parent=styles["Title"], alignment=TA_LEFT, fontSize=19, leading=22))

    story = []
    header = Table(
        [
            [Paragraph("<b>GMAO Pro</b>", styles["TitleLeft"]), Paragraph(f"<b>BON DE COMMANDE</b><br/>{order[1]}", styles["Right"])],
            [Paragraph("Commande fournisseur", styles["Small"]), Paragraph(f"Date : {order[5]}<br/>Statut : {order[4]}", styles["Right"])],
        ],
        colWidths=[105 * mm, 60 * mm],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story.extend([header, Spacer(1, 7 * mm)])

    supplier_lines = [f"<b>{order[3]}</b>"]
    if order[14]: supplier_lines.append(str(order[14]).replace("\n", "<br/>"))
    if order[15]: supplier_lines.append(f"SIRET : {order[15]}")
    if order[16]: supplier_lines.append(str(order[16]))
    if order[17]: supplier_lines.append(str(order[17]))

    delivery = str(order[7] or "Adresse de livraison non précisée").replace("\n", "<br/>")
    info = Table(
        [[Paragraph("<b>FOURNISSEUR</b><br/>" + "<br/>".join(supplier_lines), styles["Small"]),
          Paragraph("<b>LIVRAISON</b><br/>" + delivery + (f"<br/><br/><b>Date souhaitée :</b> {order[6]}" if order[6] else ""), styles["Small"])]],
        colWidths=[82.5 * mm, 82.5 * mm],
    )
    info.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E2E8F0")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 7),
    ]))
    story.extend([info, Spacer(1, 7 * mm)])

    data = [["Réf. interne", "Réf. fournisseur", "Désignation", "Qté", "PU HT", "Rem.", "TVA", "Total HT"]]
    for line in lines:
        qty = float(line[4] or 0)
        price = float(line[6] or 0)
        discount = float(line[7] or 0)
        net = qty * price * (1 - discount / 100)
        data.append([
            str(line[1]),
            str(line[3] or "-"),
            Paragraph(str(line[2]), styles["Small"]),
            f"{qty:g}",
            _money(price),
            f"{discount:g}%",
            f"{float(line[8] or 0):g}%",
            _money(net),
        ])

    lines_table = Table(data, repeatRows=1, colWidths=[21*mm, 26*mm, 42*mm, 13*mm, 21*mm, 14*mm, 14*mm, 24*mm])
    lines_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([lines_table, Spacer(1, 6 * mm)])

    totals_table = Table([
        ["Sous-total HT", _money(totals.get("subtotal_ht"))],
        ["Frais de port HT", _money(totals.get("shipping_ht"))],
        ["TVA", _money(totals.get("vat"))],
        ["TOTAL TTC", _money(totals.get("total_ttc"))],
    ], colWidths=[40 * mm, 30 * mm], hAlign="RIGHT")
    totals_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("LINEABOVE", (0, 3), (-1, 3), 1, colors.HexColor("#1E293B")),
        ("FONTNAME", (0, 3), (-1, 3), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(totals_table)

    if order[10]:
        story.extend([Spacer(1, 8 * mm), Paragraph("<b>Observations / conditions</b>", styles["Heading3"]), Paragraph(str(order[10]).replace("\n", "<br/>"), styles["Small"])])

    story.extend([
        Spacer(1, 10 * mm),
        Paragraph(f"Créé par : {order[12]} &nbsp;&nbsp;&nbsp;|&nbsp;&nbsp;&nbsp; Validé par : {order[13]}", styles["Small"]),
    ])
    doc.build(story)
    output.seek(0)
    return output

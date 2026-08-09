from datetime import datetime, date, time
from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAVY = colors.HexColor("#172554")
RED = colors.HexColor("#B91C1C")
DARK = colors.HexColor("#111827")
SLATE = colors.HexColor("#475569")
LINE = colors.HexColor("#CBD5E1")
LIGHT = colors.HexColor("#F3F4F6")
LIGHTER = colors.HexColor("#F8FAFC")
WHITE = colors.white
GREEN = colors.HexColor("#15803D")
AMBER = colors.HexColor("#B45309")
DANGER = colors.HexColor("#B91C1C")

STATE_LABELS = {
    "Opérationnel": ("OPÉRATIONNEL", GREEN),
    "Nécessite un suivi": ("SUIVI REQUIS", AMBER),
    "Toujours en panne": ("TOUJOURS EN PANNE", DANGER),
}


def _safe(value, default="-"):
    if value is None or value == "":
        return default
    return str(value)


def _p(value, style):
    return Paragraph(escape(_safe(value)).replace("\n", "<br/>"), style)


def _format_date(value):
    if not value:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], fmt).strftime("%d/%m/%Y")
        except ValueError:
            pass
    return raw


def _format_datetime(value):
    if not value:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    raw = str(value).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(raw, fmt).strftime("%d/%m/%Y %H:%M")
        except ValueError:
            pass
    return raw


def _format_time(value):
    if not value:
        return "-"
    if isinstance(value, time):
        return value.strftime("%H:%M")
    raw = str(value).strip()
    return raw[:5] if len(raw) >= 5 else raw


def _minutes_between(start, end):
    if not start or not end:
        return None
    try:
        s = datetime.strptime(_format_time(start), "%H:%M")
        e = datetime.strptime(_format_time(end), "%H:%M")
        if e < s:
            e = e.replace(day=e.day + 1)
        return int((e - s).total_seconds() // 60)
    except Exception:
        return None


def _duration_label(minutes):
    if minutes is None:
        return "-"
    try:
        minutes = int(minutes)
    except Exception:
        return _safe(minutes)
    h, m = divmod(max(0, minutes), 60)
    return f"{h:02d}:{m:02d}"


def _section_title(text, styles):
    return Paragraph(escape(text), styles["section"])


def _label_value_table(rows, styles, widths=(42 * mm, 130 * mm)):
    data = [[_p(label, styles["label"]), _p(value, styles["body"])] for label, value in rows]
    table = Table(data, colWidths=list(widths), hAlign="LEFT")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _narrative_block(label, value, styles):
    table = Table(
        [[_p(label, styles["label"]), _p(value, styles["narrative"])]],
        colWidths=[48 * mm, 124 * mm],
        hAlign="LEFT",
    )
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def _materials_table(items, styles):
    data = [[
        _p("DESCRIPTION", styles["table_header"]),
        _p("RÉFÉRENCE", styles["table_header"]),
        _p("QUANTITÉ", styles["table_header"]),
    ]]
    if items:
        for item in items:
            data.append([
                _p(item.get("designation"), styles["table_body"]),
                _p(item.get("reference"), styles["table_body"]),
                _p(item.get("quantite"), styles["table_body_right"]),
            ])
    else:
        data.append([
            _p("Aucune pièce ou matériel consommé enregistré.", styles["table_body"]),
            "",
            "",
        ])

    table = Table(data, colWidths=[108 * mm, 42 * mm, 22 * mm], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 1.4, NAVY),
        ("LINEBELOW", (0, -1), (-1, -1), 1.0, NAVY),
        ("GRID", (0, 0), (-1, -1), 0.35, LINE),
        ("BACKGROUND", (0, 1), (-1, -1), LIGHTER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _timesheet_table(data, styles):
    actual_minutes = _minutes_between(data.get("heure_debut"), data.get("heure_fin"))
    if actual_minutes is None:
        actual_minutes = data.get("estimated_duration")

    technician = data.get("technician_name") or data.get("author") or "-"
    row = [
        _format_date(data.get("work_date") or data.get("scheduled_date") or data.get("created_at")),
        technician,
        "INTERVENTION",
        _format_time(data.get("heure_debut")),
        _format_time(data.get("heure_fin")),
        _duration_label(actual_minutes),
    ]
    headers = ["DATE", "TECHNICIEN", "DESCRIPTION", "DÉBUT", "FIN", "TEMPS PASSÉ"]
    table_data = [
        [_p(h, styles["table_header"]) for h in headers],
        [_p(v, styles["table_body"]) for v in row],
    ]
    table = Table(
        table_data,
        colWidths=[25 * mm, 42 * mm, 39 * mm, 20 * mm, 20 * mm, 26 * mm],
        hAlign="LEFT",
    )
    table.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 1.4, NAVY),
        ("LINEBELOW", (0, -1), (-1, -1), 1.0, NAVY),
        ("BACKGROUND", (0, 1), (-1, 1), LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return table


def create_intervention_report_pdf(data, materials=None):
    """Génère le modèle PDF uniforme d'un rapport d'intervention."""
    output = BytesIO()
    report_id = data.get("report_id") or data.get("id") or "-"
    intervention_title = _safe(data.get("intervention_title"), "Intervention")

    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=38 * mm,
        bottomMargin=22 * mm,
        title=f"Rapport d'intervention #{report_id}",
        author="GMAO",
    )

    base = getSampleStyleSheet()
    styles = {
        "label": ParagraphStyle("IRLabel", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=DARK),
        "body": ParagraphStyle("IRBody", parent=base["Normal"], fontSize=9.2, leading=12, textColor=DARK),
        "narrative": ParagraphStyle("IRNarrative", parent=base["Normal"], fontSize=9.5, leading=14, textColor=DARK),
        "report_title": ParagraphStyle("IRTitle", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=RED, spaceAfter=0),
        "work_title": ParagraphStyle("IRWorkTitle", parent=base["Heading2"], fontName="Helvetica", fontSize=19, leading=22, textColor=RED, spaceAfter=0),
        "section": ParagraphStyle("IRSection", parent=base["Heading2"], fontName="Helvetica", fontSize=17, leading=20, textColor=RED, spaceBefore=0, spaceAfter=0),
        "table_header": ParagraphStyle("IRTableHeader", parent=base["Normal"], fontSize=8.5, leading=10, textColor=DARK),
        "table_body": ParagraphStyle("IRTableBody", parent=base["Normal"], fontSize=8.7, leading=11, textColor=DARK),
        "table_body_right": ParagraphStyle("IRTableBodyRight", parent=base["Normal"], fontSize=8.7, leading=11, textColor=DARK, alignment=TA_RIGHT),
        "signature": ParagraphStyle("IRSignature", parent=base["Normal"], fontSize=10, leading=13, textColor=DARK, alignment=TA_CENTER),
        "state": ParagraphStyle("IRState", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=WHITE, alignment=TA_CENTER),
    }

    def draw_brand_and_footer(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(RED)
        x = 18 * mm
        y = A4[1] - 23 * mm
        canvas.roundRect(x, y, 9 * mm, 9 * mm, 1.5 * mm, fill=1, stroke=0)
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica-Bold", 12)
        canvas.drawCentredString(x + 4.5 * mm, y + 2.7 * mm, "G")
        canvas.setFillColor(NAVY)
        canvas.setFont("Helvetica-Bold", 22)
        canvas.drawString(31 * mm, A4[1] - 19.5 * mm, "GMAO")
        canvas.setFillColor(SLATE)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(31 * mm, A4[1] - 24 * mm, "Rapports maintenance standardisés")

        canvas.setStrokeColor(NAVY)
        canvas.setLineWidth(0.45)
        canvas.line(18 * mm, 16 * mm, 192 * mm, 16 * mm)
        canvas.setFillColor(SLATE)
        canvas.setFont("Helvetica", 6.8)
        canvas.drawCentredString(105 * mm, 11.7 * mm, "GMAO - Rapport d'intervention")
        canvas.drawRightString(192 * mm, 7.8 * mm, str(doc.page))
        canvas.restoreState()

    technician = data.get("technician_name") or data.get("author") or "-"
    client_lines = [_safe(data.get("client_name"))]
    if data.get("client_email"):
        client_lines.append(str(data["client_email"]))
    if data.get("client_phone"):
        client_lines.append(str(data["client_phone"]))

    state_label, state_color = STATE_LABELS.get(
        _safe(data.get("etat")),
        (_safe(data.get("etat")).upper(), SLATE),
    )
    state_badge = Table([[_p(state_label, styles["state"])]], colWidths=[35 * mm], rowHeights=[8 * mm])
    state_badge.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), state_color),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    left_meta = _label_value_table([
        ("N° de rapport:", f"#{report_id}"),
        ("Technicien:", technician),
        ("Machine:", data.get("equipment_name")),
        ("Numéro de série:", data.get("serial_number")),
        ("Type machine:", data.get("equipment_type")),
        ("Code machine:", data.get("equipment_code")),
    ], styles, widths=(34 * mm, 58 * mm))

    right_meta = _label_value_table([
        ("Client :", "\n".join(client_lines)),
        ("Emplacement:", data.get("equipment_location")),
        ("Date prévue:", _format_date(data.get("scheduled_date"))),
        ("Type intervention:", data.get("intervention_type")),
        ("Priorité:", data.get("priority")),
        ("État du rapport:", state_label),
    ], styles, widths=(28 * mm, 52 * mm))

    cover_meta = Table([[left_meta, right_meta]], colWidths=[93 * mm, 80 * mm], hAlign="LEFT")
    cover_meta.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    story = [cover_meta, Spacer(1, 4 * mm), state_badge, Spacer(1, 6 * mm)]
    story.append(Paragraph(f"Rapport d'intervention : {escape(intervention_title)}", styles["report_title"]))
    story.append(Spacer(1, 11 * mm))
    story.append(Paragraph("Feuille de travail", styles["work_title"]))
    story.append(PageBreak())

    story.extend([
        _narrative_block("Travaux réalisés", data.get("travaux"), styles),
        Spacer(1, 7 * mm),
        _narrative_block("Observations", data.get("observations"), styles),
        Spacer(1, 7 * mm),
        _narrative_block("Recommandations", data.get("recommandations"), styles),
        Spacer(1, 14 * mm),
        _section_title("Temps & Matériel", styles),
        Spacer(1, 3 * mm),
        _materials_table(materials or [], styles),
        Spacer(1, 10 * mm),
        _section_title("Feuille de temps", styles),
        Spacer(1, 3 * mm),
        _timesheet_table(data, styles),
        Spacer(1, 9 * mm),
        _label_value_table([
            ("État après intervention:", data.get("etat")),
            ("Rapport créé le:", _format_datetime(data.get("created_at"))),
            ("Auteur du rapport:", data.get("author")),
        ], styles, widths=(45 * mm, 127 * mm)),
        PageBreak(),
    ])

    signature_name = technician if technician != "-" else _safe(data.get("author"))
    signature_box = Table([
        [Paragraph("Signature", styles["signature"])],
        [Spacer(1, 28 * mm)],
        [Paragraph("__________________________________", styles["signature"])],
        [Paragraph(escape(signature_name), styles["signature"])],
    ], colWidths=[70 * mm], rowHeights=[8 * mm, 32 * mm, 8 * mm, 10 * mm], hAlign="RIGHT")
    signature_box.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    story.extend([Spacer(1, 10 * mm), signature_box])

    document.build(story, onFirstPage=draw_brand_and_footer, onLaterPages=draw_brand_and_footer)
    output.seek(0)
    return output

from datetime import datetime
from html import escape
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image as RLImage,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


BLUE = colors.HexColor("#2563EB")
DARK_BLUE = colors.HexColor("#1E3A8A")
DARK = colors.HexColor("#0F172A")
SLATE = colors.HexColor("#475569")
LIGHT = colors.HexColor("#F8FAFC")
LABEL_BG = colors.HexColor("#EEF2FF")
LINE = colors.HexColor("#CBD5E1")
WHITE = colors.white

STATUS = {
    "pending": ("EN ATTENTE", colors.HexColor("#F59E0B")),
    "in_progress": ("EN COURS", BLUE),
    "resolved": ("RÉSOLUE", colors.HexColor("#16A34A")),
    "rejected": ("REJETÉE", colors.HexColor("#DC2626")),
}

URGENCY = {
    "low": ("BASSE", colors.HexColor("#16A34A")),
    "medium": ("MOYENNE", colors.HexColor("#F59E0B")),
    "high": ("HAUTE", colors.HexColor("#EA580C")),
    "critical": ("CRITIQUE", colors.HexColor("#DC2626")),
}


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


def _paragraph(value, style):
    text = "-" if value in (None, "") else str(value)
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def _section(title, styles):
    return Paragraph(escape(title.upper()), styles["section"])


def _info_table(rows, styles):
    data = []
    for label, value in rows:
        data.append(
            [
                _paragraph(label, styles["label"]),
                _paragraph(value, styles["value"]),
            ]
        )

    table = Table(data, colWidths=[45 * mm, 130 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), LABEL_BG),
                ("GRID", (0, 0), (-1, -1), 0.5, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _resolve_photo_path(raw_path, base_dir):
    path = Path(str(raw_path))
    if not path.is_absolute():
        path = Path(base_dir) / path
    return path


def _photo_flowables(photo_paths, base_dir, styles):
    cells = []

    for raw_path in photo_paths or []:
        if not raw_path:
            continue
        path = _resolve_photo_path(raw_path, base_dir)
        if not path.is_file():
            continue

        try:
            image_width, image_height = ImageReader(str(path)).getSize()
            max_width = 80 * mm
            max_height = 52 * mm
            ratio = min(max_width / image_width, max_height / image_height)

            image = RLImage(
                str(path),
                width=image_width * ratio,
                height=image_height * ratio,
            )
            cell = KeepTogether(
                [
                    image,
                    Spacer(1, 2 * mm),
                    Paragraph(escape(path.name), styles["caption"]),
                ]
            )
            cells.append(cell)
        except Exception:
            # Une photo invalide ne doit jamais empêcher l'export du PDF.
            continue

    if not cells:
        return []

    rows = []
    for index in range(0, len(cells), 2):
        row = cells[index : index + 2]
        if len(row) == 1:
            row.append("")
        rows.append(row)

    photos_table = Table(rows, colWidths=[87.5 * mm, 87.5 * mm], hAlign="LEFT")
    photos_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    return [
        Spacer(1, 4 * mm),
        _section("Photos jointes", styles),
        Spacer(1, 2 * mm),
        photos_table,
    ]


def create_declaration_pdf(data, photo_paths=None, base_dir="."):
    """Génère un PDF A4 uniforme pour une déclaration de panne.

    Le design est fixe dans ce fichier. Seules les données transmises dans
    ``data`` et les photos jointes changent d'une déclaration à l'autre.
    """

    output = BytesIO()
    declaration_id = data.get("id") or "-"

    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=17 * mm,
        leftMargin=17 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=f"Déclaration de panne #{declaration_id}",
        author="GMAO",
    )

    base_styles = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "DeclarationPdfTitle",
            parent=base_styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=21,
            textColor=WHITE,
            alignment=TA_LEFT,
            spaceAfter=0,
        ),
        "subtitle": ParagraphStyle(
            "DeclarationPdfSubtitle",
            parent=base_styles["Normal"],
            fontSize=9,
            leading=11,
            textColor=colors.HexColor("#DBEAFE"),
        ),
        "section": ParagraphStyle(
            "DeclarationPdfSection",
            parent=base_styles["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=9.5,
            leading=12,
            textColor=DARK,
            spaceBefore=0,
            spaceAfter=0,
        ),
        "label": ParagraphStyle(
            "DeclarationPdfLabel",
            parent=base_styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=11,
            textColor=DARK,
        ),
        "value": ParagraphStyle(
            "DeclarationPdfValue",
            parent=base_styles["Normal"],
            fontSize=9,
            leading=12,
            textColor=DARK,
        ),
        "body": ParagraphStyle(
            "DeclarationPdfBody",
            parent=base_styles["Normal"],
            fontSize=9.5,
            leading=14,
            textColor=DARK,
        ),
        "caption": ParagraphStyle(
            "DeclarationPdfCaption",
            parent=base_styles["Normal"],
            fontSize=7,
            leading=9,
            textColor=SLATE,
            alignment=TA_CENTER,
        ),
        "badge": ParagraphStyle(
            "DeclarationPdfBadge",
            parent=base_styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=10,
            textColor=WHITE,
            alignment=TA_CENTER,
        ),
    }

    status_label, status_color = STATUS.get(
        str(data.get("status") or "").lower(),
        (str(data.get("status") or "-").upper(), SLATE),
    )
    urgency_label, _ = URGENCY.get(
        str(data.get("urgency") or "").lower(),
        (str(data.get("urgency") or "-").upper(), SLATE),
    )

    header_left = [
        Paragraph("DÉCLARATION DE PANNE", styles["title"]),
        Spacer(1, 1.5 * mm),
        Paragraph("Document généré automatiquement par la GMAO", styles["subtitle"]),
    ]

    badge = Table(
        [
            [Paragraph(f"N° {escape(str(declaration_id))}", styles["badge"])],
            [Paragraph(status_label, styles["badge"])],
        ],
        colWidths=[38 * mm],
        rowHeights=[9 * mm, 9 * mm],
    )
    badge.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, 0), DARK_BLUE),
                ("BACKGROUND", (0, 1), (0, 1), status_color),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )

    header = Table([[header_left, badge]], colWidths=[137 * mm, 38 * mm])
    header.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), BLUE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (0, 0), 10),
                ("RIGHTPADDING", (0, 0), (0, 0), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (1, 0), (1, 0), 0),
                ("RIGHTPADDING", (1, 0), (1, 0), 0),
            ]
        )
    )

    story = [header, Spacer(1, 6 * mm)]

    story.extend(
        [
            _section("Identification", styles),
            Spacer(1, 2 * mm),
            _info_table(
                [
                    ("Client", data.get("client_nom")),
                    ("Équipement", data.get("equipement_nom")),
                    ("Code équipement", data.get("equipement_code")),
                    ("Type", data.get("equipement_type")),
                    ("Emplacement équipement", data.get("equipement_emplacement")),
                    ("N° série", data.get("numero_serie")),
                    (
                        "Fabricant / Modèle",
                        " / ".join(
                            value
                            for value in [data.get("fabricant"), data.get("modele")]
                            if value
                        )
                        or "-",
                    ),
                ],
                styles,
            ),
            Spacer(1, 5 * mm),
        ]
    )

    declarant = data.get("declared_by_name") or data.get("username") or "-"
    intervention_label = "-"
    if data.get("intervention_id"):
        intervention_label = f"#{data['intervention_id']}"
        if data.get("intervention_title"):
            intervention_label += f" - {data['intervention_title']}"

    story.extend(
        [
            _section("Déclaration", styles),
            Spacer(1, 2 * mm),
            _info_table(
                [
                    ("Titre", data.get("title")),
                    ("Déclaré par", declarant),
                    ("Date de création", _format_datetime(data.get("created_at"))),
                    ("Localisation signalée", data.get("location")),
                    ("Niveau d'urgence", urgency_label),
                    ("Intervention liée", intervention_label),
                ],
                styles,
            ),
            Spacer(1, 5 * mm),
            _section("Description de la panne", styles),
            Spacer(1, 2 * mm),
        ]
    )

    description = Table(
        [[_paragraph(data.get("description"), styles["body"])]],
        colWidths=[175 * mm],
    )
    description.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.7, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(description)
    story.extend(_photo_flowables(photo_paths, base_dir, styles))

    def draw_footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(17 * mm, 13 * mm, 193 * mm, 13 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(SLATE)
        canvas.drawString(17 * mm, 8.5 * mm, "GMAO - Déclaration de panne")
        canvas.drawRightString(193 * mm, 8.5 * mm, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    output.seek(0)
    return output

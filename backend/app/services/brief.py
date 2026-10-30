"""Evidence-bound triage and PDF brief generation."""

import re
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    LongTable,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.security.field_encryption import FieldEncryptor
from app.services.case_taxonomy import matter_type_from_call

DISCLAIMER = (
    "AI-assisted intake summary for lawyer review only. Caller statements and extracted facts "
    "are unverified. This document is not legal advice, a legal opinion, or confirmation of "
    "a lawyer-client relationship. Verify all facts, dates, identities, and applicable law "
    "before use."
)

_FONT_PATH = Path(__file__).resolve().parents[1] / "assets" / "NotoSansDevanagari-Regular.ttf"
pdfmetrics.registerFont(TTFont("NotoDevanagari", str(_FONT_PATH)))


def triage(document: dict, encryptor: FieldEncryptor | None) -> dict:
    intake = document.get("intake", {})
    statements = []
    content_unavailable = False
    for segment in intake.get("transcript_segments", []):
        if segment.get("speaker") == "caller" and segment.get("text_encrypted"):
            try:
                text = encryptor.decrypt(segment["text_encrypted"]) if encryptor else None
            except ValueError:
                text = None
            if text is None:
                content_unavailable = True
            else:
                statements.append({"id": str(segment.get("id", "")), "text": text})
    facts = []
    for fact in intake.get("facts", []):
        try:
            value = (
                encryptor.decrypt(fact["value_encrypted"])
                if encryptor and fact.get("value_encrypted")
                else None
            )
        except ValueError:
            value = None
        if fact.get("value_encrypted") and value is None:
            content_unavailable = True
        facts.append(
            {
                "field": fact.get("field_name", "Fact"),
                "value": value,
                "providedByCaller": fact.get("provided_by_caller", True),
                "notProvided": bool(fact.get("not_provided")),
                "sourceIds": [
                    str(value) for value in fact.get("source_transcript_segment_ids", [])
                ],
            }
        )
    encrypted_overview = intake.get("triage_summary_encrypted")
    try:
        overview = (
            encryptor.decrypt(encrypted_overview) if encryptor and encrypted_overview else None
        )
    except ValueError:
        overview = None
    if encrypted_overview and overview is None:
        content_unavailable = True
    fact_values = {item["field"].casefold(): item["value"] for item in facts if item["value"]}
    matter_type = matter_type_from_call(
        intake.get("matter_type"),
        fact_values.get("matter type"),
        [item["text"] for item in statements],
    )
    if not overview:
        timeline = fact_values.get("timeline of events")
        help_wanted = fact_values.get("help the caller wants")
        if timeline or help_wanted:
            parts = []
            if timeline:
                parts.append(f"The caller described the following events: {timeline.rstrip('.')}.")
            if help_wanted:
                parts.append(f"The caller seeks help with {help_wanted.rstrip('.')}.")
            overview = " ".join(parts)
        else:
            overview = "A triage summary is not available for this call."
    return {
        "matterType": matter_type,
        "language": document.get("language", {}).get("preferred"),
        "overview": overview,
        "contentUnavailable": content_unavailable,
        "callerStatements": statements,
        "facts": facts,
        "disclaimer": DISCLAIMER,
    }


_PAGE_W, _PAGE_H = 595.28, 841.89
_MARGIN = 47
_WIDTH = _PAGE_W - 2 * _MARGIN
_INK = colors.HexColor("#1D3238")
_TEAL = colors.HexColor("#205F5B")
_GOLD = colors.HexColor("#BA8651")
_MUTED = colors.HexColor("#60737A")
_PAPER = colors.HexColor("#F8F7F3")
_PALE = colors.HexColor("#EAF0EC")
_LINE = colors.HexColor("#D6DEDA")
_DATE_TOKEN = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"(?:\s+\d{1,2})?(?:,?\s+\d{4})?\b|"
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}\b|"
    r"\b(?:today|yesterday|last week|last month|now|currently)\b",
    re.IGNORECASE,
)

_FACT_GROUPS = (
    (
        "People and relationships",
        (
            "Caller name",
            "Caller location",
            "Other people named",
            "Relationship and marriage",
        ),
    ),
    (
        "Children, home and finances",
        (
            "Children and care",
            "Housing and living arrangements",
            "Property, assets and debts",
            "Financial support",
        ),
    ),
    (
        "Proceedings and urgency",
        (
            "Estate and will",
            "Urgent concerns or deadlines",
            "Court proceedings and orders",
        ),
    ),
    (
        "Evidence and request",
        (
            "Timeline of events",
            "Documents and evidence",
            "Help the caller wants",
        ),
    ),
)


def _brief_styles() -> dict[str, ParagraphStyle]:
    return {
        "section": ParagraphStyle(
            "section",
            fontName="Helvetica-Bold",
            fontSize=8.3,
            leading=12,
            textColor=_TEAL,
            spaceAfter=10,
        ),
        "subhead": ParagraphStyle(
            "subhead",
            fontName="Times-Bold",
            fontSize=16,
            leading=19,
            textColor=_INK,
            spaceAfter=8,
        ),
        "page_title": ParagraphStyle(
            "page_title",
            fontName="Times-Bold",
            fontSize=27,
            leading=32,
            textColor=_INK,
            spaceAfter=5,
        ),
        "small": ParagraphStyle(
            "small",
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=_MUTED,
            spaceAfter=14,
        ),
        "body": ParagraphStyle(
            "body",
            fontName="NotoDevanagari",
            fontSize=9.5,
            leading=14.5,
            textColor=_INK,
            shaping=1,
        ),
        "value": ParagraphStyle(
            "value",
            fontName="NotoDevanagari",
            fontSize=8.7,
            leading=12.5,
            textColor=_INK,
            shaping=1,
        ),
        "label": ParagraphStyle(
            "label",
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=12,
            textColor=_INK,
        ),
        "source": ParagraphStyle(
            "source",
            fontName="Helvetica-Bold",
            fontSize=7,
            leading=11,
            textColor=_TEAL,
            alignment=2,
        ),
        "card_label": ParagraphStyle(
            "card_label",
            fontName="Helvetica-Bold",
            fontSize=7.4,
            leading=11,
            textColor=_MUTED,
            spaceAfter=5,
        ),
        "card_value": ParagraphStyle(
            "card_value",
            fontName="NotoDevanagari",
            fontSize=10,
            leading=13,
            textColor=_INK,
            shaping=1,
        ),
        "group": ParagraphStyle(
            "group",
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=11,
            textColor=colors.white,
        ),
    }


def _brief_paragraph(value: object, style: ParagraphStyle) -> Paragraph:
    """Escape call content while retaining the existing Devanagari shaping behavior."""
    chunks = re.split(r"([\u0900-\u097f]+)", str(value or ""))
    content = "".join(
        escape(chunk)
        if re.search(r"[\u0900-\u097f]", chunk)
        else f'<font name="Helvetica">{escape(chunk)}</font>'
        for chunk in chunks
        if chunk
    )
    return Paragraph(content or " ", style)


def _source_label(fact: dict) -> str:
    if fact.get("notProvided") or not fact.get("value"):
        return "MISSING"
    if fact.get("providedByCaller") is False:
        return "CORRECTED"
    if fact.get("sourceIds"):
        return "CALL"
    return "INTAKE"


def _chronology_entries(value: str) -> tuple[list[tuple[str, str]], bool]:
    """Break the saved timeline into dated statements without adding dates or events."""
    parts = [part.strip() for part in re.split(r"(?<=[.!?।])\s+|;\s*", value) if part.strip()]
    entries = []
    truncated = len(parts) > 4
    for part in parts[:4]:
        date = _DATE_TOKEN.search(part)
        label = date.group(0).upper() if date else "DATE NOT STATED"
        if len(part) > 230:
            part = part[:227].rsplit(" ", 1)[0] + "..."
            truncated = True
        entries.append((label, part))
    return entries, truncated


class _Chronology(Flowable):
    """A compact date rail for the first-page call chronology."""

    def __init__(self, entries: list[tuple[str, str]], truncated: bool, style: ParagraphStyle):
        super().__init__()
        self.entries = entries
        self.truncated = truncated
        self.style = style
        self.paragraphs: list[Paragraph] = []
        self.row_heights: list[float] = []

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = avail_width
        self.paragraphs = [_brief_paragraph(event, self.style) for _, event in self.entries]
        self.row_heights = [
            max(31, paragraph.wrap(avail_width - 165, avail_height)[1] + 13)
            for paragraph in self.paragraphs
        ]
        self.height = 22 + sum(self.row_heights) + (18 if self.truncated else 0)
        return self.width, self.height

    def draw(self) -> None:
        canv = self.canv
        canv.setFillColor(colors.white)
        canv.setStrokeColor(_LINE)
        canv.roundRect(0, 0, self.width, self.height, 6, fill=1, stroke=1)
        marker_positions = []
        top = self.height - 15
        for (label, _), paragraph, row_height in zip(
            self.entries, self.paragraphs, self.row_heights, strict=True
        ):
            marker_positions.append(top - 10)
            canv.setFont("Helvetica-Bold", 7.6)
            canv.setFillColor(_TEAL)
            canv.drawString(42, top - 12, label)
            paragraph.drawOn(canv, 151, top - 2 - paragraph.height)
            top -= row_height
        if len(marker_positions) > 1:
            canv.setStrokeColor(_LINE)
            canv.setLineWidth(1.5)
            canv.line(22, marker_positions[0], 22, marker_positions[-1])
        canv.setFillColor(_GOLD)
        for marker_y in marker_positions:
            canv.circle(22, marker_y, 3.8, fill=1, stroke=0)
        if self.truncated:
            canv.setFont("Helvetica", 7.5)
            canv.setFillColor(_MUTED)
            canv.drawString(42, 10, "Full chronology remains in the Case Fact Sheet.")


def _brief_page(canvas, doc, *, first: bool, version: int, firm_name: str, call_id: str):
    canvas.saveState()
    canvas.setFillColor(_PAPER)
    canvas.rect(0, 0, _PAGE_W, _PAGE_H, fill=1, stroke=0)
    if first:
        canvas.setFillColor(_INK)
        canvas.rect(0, _PAGE_H - 167, _PAGE_W, 167, fill=1, stroke=0)
        canvas.setFillColor(_GOLD)
        canvas.rect(_MARGIN, _PAGE_H - 46, 3, 17, fill=1, stroke=0)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(colors.HexColor("#C5D8D2"))
        canvas.drawString(_MARGIN + 13, _PAGE_H - 40, "FAMILY LAW  /  INTAKE RECORD")
        canvas.setFillColor(colors.white)
        canvas.setFont("Times-Bold", 32)
        canvas.drawString(_MARGIN, _PAGE_H - 91, "Intake brief")
        canvas.setFont("Helvetica", 10.5)
        canvas.drawString(_MARGIN, _PAGE_H - 116, "A focused record for the reviewing lawyer")
        canvas.setStrokeColor(colors.HexColor("#5E7475"))
        canvas.line(_MARGIN, _PAGE_H - 135, _PAGE_W - _MARGIN, _PAGE_H - 135)
        canvas.setFont("Helvetica-Bold", 8.5)
        canvas.drawString(_MARGIN, _PAGE_H - 153, f"Version {version}  /  {firm_name[:52]}")
        canvas.drawRightString(_PAGE_W - _MARGIN, _PAGE_H - 153, f"CALL {call_id[:8]}")
    else:
        canvas.setFillColor(_INK)
        canvas.rect(0, _PAGE_H - 11, _PAGE_W, 11, fill=1, stroke=0)
        canvas.setFillColor(_TEAL)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawString(_MARGIN, _PAGE_H - 43, "CASE INFORMATION  /  INTAKE BRIEF")
        canvas.setFillColor(_MUTED)
        canvas.drawRightString(_PAGE_W - _MARGIN, _PAGE_H - 43, f"Version {version}")

    # The disclaimer stays on every page, including long reports that flow past page two.
    canvas.setFillColor(_PALE)
    canvas.roundRect(_MARGIN, 67, _WIDTH, 66, 6, fill=1, stroke=0)
    canvas.setFillColor(_GOLD)
    canvas.rect(_MARGIN, 67, 4, 66, fill=1, stroke=0)
    canvas.setFillColor(_TEAL)
    canvas.setFont("Helvetica-Bold", 7.5)
    canvas.drawString(_MARGIN + 15, 115, "USE AND VERIFICATION")
    footer_style = ParagraphStyle(
        "footer", fontName="Helvetica", fontSize=7.6, leading=10.2, textColor=_INK
    )
    footer = Paragraph(escape(DISCLAIMER), footer_style)
    _, height = footer.wrap(_WIDTH - 30, 42)
    footer.drawOn(canvas, _MARGIN + 15, 106 - height)
    canvas.setStrokeColor(_LINE)
    canvas.line(_MARGIN, 52, _PAGE_W - _MARGIN, 52)
    canvas.setFillColor(_MUTED)
    canvas.setFont("Helvetica-Bold", 7)
    canvas.drawString(_MARGIN, 36, "CONFIDENTIAL  /  LAWYER REVIEW")
    canvas.drawRightString(_PAGE_W - _MARGIN, 36, f"PAGE {doc.page}")
    canvas.restoreState()


def render_brief(document: dict, summary: dict, version: int, firm_name: str) -> bytes:
    """Render a versioned, source-labelled brief from the saved intake record."""
    output = BytesIO()
    call_id = str(document.get("id") or "")
    pdf = BaseDocTemplate(
        output,
        pagesize=(_PAGE_W, _PAGE_H),
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=0,
        bottomMargin=0,
        title=f"Intake brief v{version}",
        author=firm_name,
    )
    first_frame = Frame(
        _MARGIN,
        145,
        _WIDTH,
        _PAGE_H - 335,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    detail_frame = Frame(
        _MARGIN,
        145,
        _WIDTH,
        _PAGE_H - 225,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    pdf.addPageTemplates(
        [
            PageTemplate(
                id="overview",
                frames=[first_frame],
                onPage=lambda canv, doc: _brief_page(
                    canv, doc, first=True, version=version, firm_name=firm_name, call_id=call_id
                ),
            ),
            PageTemplate(
                id="facts",
                frames=[detail_frame],
                onPage=lambda canv, doc: _brief_page(
                    canv, doc, first=False, version=version, firm_name=firm_name, call_id=call_id
                ),
            ),
        ]
    )
    styles = _brief_styles()

    def p(value: object, kind: str = "body") -> Paragraph:
        return _brief_paragraph(value, styles[kind])

    facts = summary.get("facts") or []
    by_name = {str(item.get("field", "")).casefold(): item for item in facts}

    def fact_value(name: str) -> str | None:
        return by_name.get(name.casefold(), {}).get("value")

    story = [p("01  /  CASE OVERVIEW", "section")]
    overview = Table(
        [
            [
                [
                    p("What happened", "subhead"),
                    p(
                        summary.get("overview")
                        or "A triage summary is not available for this call."
                    ),
                ]
            ]
        ],
        colWidths=[_WIDTH],
        hAlign="LEFT",
    )
    overview.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("LINEBEFORE", (0, 0), (0, -1), 4, _GOLD),
                ("BOX", (0, 0), (-1, -1), 0.5, _LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 19),
                ("RIGHTPADDING", (0, 0), (-1, -1), 19),
                ("TOPPADDING", (0, 0), (-1, -1), 16),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 17),
            ]
        )
    )
    story.extend([overview, Spacer(1, 18), p("02  /  AT A GLANCE", "section")])
    glance = (
        ("CALLER", fact_value("Caller name") or "Not provided"),
        ("MATTER", summary.get("matterType") or "Not identified"),
        ("LOCATION", fact_value("Caller location") or "Not provided"),
        ("LANGUAGE", summary.get("language") or "Not recorded"),
    )
    cards = Table(
        [[[p(label, "card_label"), p(value, "card_value")] for label, value in glance]],
        colWidths=[_WIDTH / 4] * 4,
        hAlign="LEFT",
    )
    cards.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _PALE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBEFORE", (1, 0), (-1, -1), 0.5, _LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 14),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 16),
            ]
        )
    )
    story.extend([cards, Spacer(1, 18)])

    story.append(p("03  /  KEY CHRONOLOGY", "section"))
    timeline = fact_value("Timeline of events")
    if timeline:
        entries, truncated = _chronology_entries(timeline)
        story.append(_Chronology(entries, truncated, styles["value"]))
    else:
        story.append(p("No chronology was captured in the saved call facts."))
    story.append(Spacer(1, 17))

    help_requested = fact_value("Help the caller wants")
    if help_requested:
        story.append(p("04  /  HELP REQUESTED", "section"))
        story.append(p(help_requested))
        story.append(Spacer(1, 17))

    missing = [
        str(item.get("field", "")).replace("_", " ")
        for item in facts
        if item.get("notProvided") or not item.get("value")
    ]
    if missing:
        story.append(p("INFORMATION TO CONFIRM", "section"))
        gap_text = ", ".join(missing[:5])
        if len(missing) > 5:
            gap_text += "; additional gaps in the fact sheet"
        story.append(p(gap_text))

    story.extend(
        [
            NextPageTemplate("facts"),
            PageBreak(),
            p("Case Fact Sheet", "page_title"),
            p(
                "Structured information captured at intake. Source labels identify how each "
                "value entered the record.",
                "small",
            ),
        ]
    )

    grouped_names = {name.casefold() for _, names in _FACT_GROUPS for name in names}
    groups = list(_FACT_GROUPS)
    extra = [item for item in facts if str(item.get("field", "")).casefold() not in grouped_names]
    if extra:
        groups.append(("Additional facts", tuple(str(item.get("field", "")) for item in extra)))
    if not facts:
        story.append(p("No structured facts were saved."))
    else:
        for group_title, names in groups:
            group_facts = [by_name[name.casefold()] for name in names if name.casefold() in by_name]
            if not group_facts:
                continue
            rows = [[p(group_title.upper(), "group"), "", ""]]
            for item in group_facts:
                value = item.get("value") or "Not provided"
                rows.append(
                    [
                        p(str(item.get("field", "Fact")).replace("_", " "), "label"),
                        p(value, "value"),
                        p(_source_label(item), "source"),
                    ]
                )
            table = LongTable(
                rows,
                colWidths=[154, 274, _WIDTH - 428],
                repeatRows=1,
                splitInRow=1,
                hAlign="LEFT",
            )
            table.setStyle(
                TableStyle(
                    [
                        ("SPAN", (0, 0), (-1, 0)),
                        ("BACKGROUND", (0, 0), (-1, 0), _INK),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        (
                            "ROWBACKGROUNDS",
                            (0, 1),
                            (-1, -1),
                            [colors.white, colors.HexColor("#F1F4F1")],
                        ),
                        ("LINEBELOW", (0, 1), (-1, -1), 0.4, _LINE),
                        ("LEFTPADDING", (0, 0), (-1, 0), 11),
                        ("TOPPADDING", (0, 0), (-1, 0), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
                        ("LEFTPADDING", (0, 1), (-1, -1), 11),
                        ("RIGHTPADDING", (0, 1), (-1, -1), 8),
                        ("TOPPADDING", (0, 1), (-1, -1), 7),
                        ("BOTTOMPADDING", (0, 1), (-1, -1), 7),
                    ]
                )
            )
            story.extend([table, Spacer(1, 5)])
        story.append(Spacer(1, 3))
        story.append(
            p(
                "SOURCE KEY  /  CALL = cited caller transcript; CORRECTED = manual correction; "
                "INTAKE = saved without a transcript citation; MISSING = not supplied.",
                "small",
            )
        )

    pdf.build(story)
    return output.getvalue()

"""Evidence-bound triage and PDF brief generation."""

import re
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph

from app.security.field_encryption import FieldEncryptor

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
                "notProvided": bool(fact.get("not_provided")),
                "sourceIds": [
                    str(value) for value in fact.get("source_transcript_segment_ids", [])
                ],
            }
        )
    return {
        "matterType": intake.get("matter_type"),
        "language": document.get("language", {}).get("preferred"),
        "overview": " ".join(item["text"] for item in statements[:3])
        or (
            "Encrypted caller content is unavailable with the configured key."
            if content_unavailable
            else "No caller statements were saved."
        ),
        "contentUnavailable": content_unavailable,
        "callerStatements": statements,
        "facts": facts,
        "disclaimer": DISCLAIMER,
    }


def render_brief(document: dict, summary: dict, version: int, firm_name: str) -> bytes:
    output = BytesIO()
    pdf = BaseDocTemplate(
        output,
        pagesize=(595.28, 841.89),
        leftMargin=54,
        rightMargin=54,
        topMargin=58,
        bottomMargin=58,
        title=f"Intake brief v{version}",
        author=firm_name,
    )
    frame = Frame(
        54, 58, 487.28, 725.89, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0
    )
    pdf.addPageTemplates(PageTemplate(id="brief", frames=[frame], onPage=_page_footer))
    ink = colors.HexColor("#20283D")
    blue = colors.HexColor("#346CBA")
    muted = colors.HexColor("#66748C")
    styles = {
        "title": ParagraphStyle(
            "title", fontName="Helvetica-Bold", fontSize=21, leading=26, textColor=ink, spaceAfter=8
        ),
        "meta": ParagraphStyle(
            "meta", fontName="Helvetica", fontSize=9, leading=14, textColor=muted, spaceAfter=14
        ),
        "heading": ParagraphStyle(
            "heading",
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=16,
            textColor=blue,
            spaceBefore=16,
            spaceAfter=7,
        ),
        "body": ParagraphStyle(
            "body",
            fontName="NotoDevanagari",
            fontSize=10,
            leading=17,
            textColor=ink,
            spaceAfter=8,
            shaping=1,
        ),
        "disclaimer": ParagraphStyle(
            "disclaimer",
            fontName="NotoDevanagari",
            fontSize=9,
            leading=14,
            textColor=ink,
            borderColor=muted,
            borderWidth=0.6,
            borderPadding=10,
            spaceBefore=20,
            shaping=1,
        ),
    }

    def p(value: object, style: str = "body") -> Paragraph:
        content = str(value or "")
        if style in {"body", "disclaimer"}:
            chunks = re.split(r"([\u0900-\u097f]+)", content)
            content = "".join(
                escape(chunk)
                if re.search(r"[\u0900-\u097f]", chunk)
                else f'<font name="Helvetica">{escape(chunk)}</font>'
                for chunk in chunks
                if chunk
            )
        else:
            content = escape(content)
        return Paragraph(content, styles[style])

    story = [
        p("Intake brief", "title"),
        p(f"Version {version}  |  {firm_name}  |  Call {document.get('id', '')}", "meta"),
        p("Triage summary", "heading"),
        p(f"Matter: {summary['matterType'] or 'Not identified'}", "body"),
        p(f"Language: {summary['language'] or 'Not recorded'}", "body"),
        p(summary["overview"], "body"),
        p("Caller statements", "heading"),
    ]
    if summary["callerStatements"]:
        story.extend(
            p(f"{index}. {item['text']}")
            for index, item in enumerate(summary["callerStatements"], 1)
        )
    else:
        story.append(p("No caller statements were saved."))
    story.append(p("Recorded facts", "heading"))
    if summary["facts"]:
        story.extend(
            p(f"{item['field'].replace('_', ' ')}: {item['value'] or 'Not provided'}")
            for item in summary["facts"]
        )
    else:
        story.append(p("No structured facts were saved."))
    story.append(p(DISCLAIMER, "disclaimer"))
    pdf.build(story)
    return output.getvalue()


def _page_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#CBD2DE"))
    canvas.line(54, 44, 541, 44)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#66748C"))
    canvas.drawString(54, 30, "Confidential intake material")
    canvas.drawRightString(541, 30, f"Page {doc.page}")
    canvas.restoreState()

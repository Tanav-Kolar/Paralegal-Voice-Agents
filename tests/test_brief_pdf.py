"""Check brief layout and pagination with realistic structured intake data."""

from io import BytesIO

from pypdf import PdfReader

from app.services.brief import _chronology_entries, render_brief
from app.services.extraction import FIELDS


def _summary(long_value: bool = False) -> dict:
    values = {
        "Caller name": "Maya Rao",
        "Caller location": "Pune, Maharashtra",
        "Other people named": "Spouse: Arjun Rao",
        "Relationship and marriage": "Married in 2017; separated in March 2026",
        "Children and care": "One child lives with the caller",
        "Housing and living arrangements": "Caller and child live separately",
        "Property, assets and debts": "Shared apartment; ownership unclear",
        "Financial support": "Caller reports irregular contributions",
        "Urgent concerns or deadlines": "School term and housing stability",
        "Timeline of events": (
            "The parties separated in March 2026. "
            "The caller and child moved to a separate home in April."
        ),
        "Court proceedings and orders": "No order reported",
        "Documents and evidence": "Marriage certificate and messages mentioned",
        "Help the caller wants": "Advice on separation, care and financial support",
    }
    if long_value:
        values["Property, assets and debts"] = "Disputed payments and arrangements. " * 90
    facts = [
        {
            "field": field,
            "value": values.get(field),
            "notProvided": field not in values,
            "providedByCaller": field != "Caller name",
            "sourceIds": ["sample"] if field in values and field != "Caller name" else [],
        }
        for field in FIELDS
    ]
    return {
        "matterType": "Divorce and separation",
        "language": "English",
        "overview": (
            "Maya Rao reports that she and her spouse separated in March 2026. "
            "Their child lives with her. She seeks advice on care and support."
        ),
        "facts": facts,
    }


def test_brief_has_two_page_overview_and_source_labelled_fact_sheet() -> None:
    pdf = render_brief({"id": "example-call-123"}, _summary(), 2, "Victus Family Law")
    pages = PdfReader(BytesIO(pdf)).pages
    text = " ".join(page.extract_text() for page in pages)

    assert len(pages) == 2
    assert "Version 2" in text
    assert "What happened" in text
    assert "KEY CHRONOLOGY" in pages[0].extract_text()
    assert "MARCH 2026" in pages[0].extract_text()
    assert "APRIL" in pages[0].extract_text()
    assert "Case Fact Sheet" in text
    assert "CORRECTED" in text and "MISSING" in text and "CALL" in text
    assert text.count("AI-assisted intake summary") == 2
    assert "Maya Rao" in text and "Divorce and separation" in text


def test_brief_keeps_long_fact_across_pages() -> None:
    pdf = render_brief({"id": "example-call-123"}, _summary(long_value=True), 3, "Firm")
    pages = PdfReader(BytesIO(pdf)).pages
    text = " ".join(page.extract_text() for page in pages)

    assert len(pages) >= 3
    assert "Help the caller wants" in text
    assert "Advice on separation, care and financial support" in text
    assert all("AI-assisted intake summary" in page.extract_text() for page in pages)


def test_chronology_builder_does_not_infer_missing_dates() -> None:
    entries, truncated = _chronology_entries(
        "The parties separated in March 2026. The caller moved in April. "
        "The caller requested support."
    )

    assert entries == [
        ("MARCH 2026", "The parties separated in March 2026."),
        ("APRIL", "The caller moved in April."),
        ("DATE NOT STATED", "The caller requested support."),
    ]
    assert not truncated

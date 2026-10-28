"""Stable portal categories for family-law intake calls."""

import re

MATTER_TYPES = (
    "Divorce and separation",
    "Child custody and visitation",
    "Maintenance and alimony",
    "Domestic violence and protection",
    "Inheritance and succession",
    "Adoption and guardianship",
    "Property division",
    "Marriage validity",
    "Other family matter",
)

_KEYWORDS = (
    (
        "Inheritance and succession",
        r"\b(inherit|succession|probate|estate|heir|last will|the will|a will)\b"
        r"|विरासत|उत्तराधिकार|वसीयत",
    ),
    (
        "Domestic violence and protection",
        r"\b(domestic violence|protection order|abuse)\b|घरेलू हिंसा",
    ),
    (
        "Adoption and guardianship",
        r"\b(adoption|adopt|guardianship|guardian)\b|गोद लेना|अभिभावक",
    ),
    ("Divorce and separation", r"\b(divorce|separat|dissolution)\w*|तलाक|अलग रह"),
    (
        "Child custody and visitation",
        r"\b(custody|visitation|child access|parenting time)\b|बच्चे की कस्टडी",
    ),
    (
        "Maintenance and alimony",
        r"\b(maintenance|alimony|spousal support|child support)\b|भरण.?पोषण",
    ),
    ("Marriage validity", r"\b(annulment|marriage validity|void marriage)\b|विवाह.*अमान्य"),
    (
        "Property division",
        r"\b(property division|marital property|shared property)\b|संपत्ति.*बंटवारा",
    ),
)


def standard_matter_type(value: str | None) -> str | None:
    """Accept a known label or map clear legacy wording to one label."""
    if not value:
        return None
    normalized = value.strip().casefold().replace("_", " ").replace("/", " ")
    for label in MATTER_TYPES:
        if normalized == label.casefold():
            return label
    for label, pattern in _KEYWORDS:
        if re.search(pattern, normalized):
            return label
    return "Other family matter" if normalized == "family" else None


def matter_type_from_call(
    stored: str | None, fact_value: str | None, caller_statements: list[str]
) -> str | None:
    """Use the recorded caller account when stored classification is absent or legacy free text."""
    generic_stored = bool(stored and stored.strip().casefold() in {"family", "other family matter"})
    if generic_stored:
        stored = None
    for candidate in (stored, fact_value, " ".join(caller_statements)):
        category = standard_matter_type(candidate)
        if category:
            return category
    return "Other family matter" if generic_stored else None

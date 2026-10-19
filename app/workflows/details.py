"""A small, consent-only flow for collecting caller-provided details."""

import re
from dataclasses import dataclass
from enum import StrEnum

from app.schemas.intake_call import LanguageChoice


class DetailState(StrEnum):
    ANSWER = "answer"
    COMPLETE = "complete"


@dataclass(frozen=True)
class DetailResult:
    prompt: str
    field: str | None = None
    value: str | None = None
    source_segment_id: str | None = None


_FIELDS = ("caller_name_as_stated", "location_as_stated", "relevant_date_as_stated")
_QUESTIONS = {
    LanguageChoice.ENGLISH: (
        "What is your name? You can say you prefer not to answer.",
        "What city or location is relevant to your matter? You can say you don't know.",
        "Is there a date relevant to your matter? Please say the date, or say you don't know.",
    ),
    LanguageChoice.HINDI: (
        "आपका नाम क्या है? यदि आप बताना नहीं चाहते तो ऐसा कह सकते हैं।",
        "आपके मामले से संबंधित शहर या स्थान कौन सा है? यदि आपको पता नहीं है तो बता सकते हैं।",
        "क्या आपके मामले से संबंधित कोई तारीख है? कृपया तारीख बताएं, या कहें कि आपको पता नहीं है।",
    ),
}
_CORRECTION = {
    LanguageChoice.ENGLISH: (
        "I didn't catch that. Please try again, or say you prefer not to answer."
    ),
    LanguageChoice.HINDI: "मैं सुन नहीं सकी। कृपया फिर से बताएं, या कहें कि आप जवाब नहीं देना चाहते।",
}
_CLOSURE = {
    LanguageChoice.ENGLISH: (
        "Thank you. This initial information will be available for the firm to review. Goodbye."
    ),
    LanguageChoice.HINDI: "धन्यवाद। यह शुरुआती जानकारी फर्म की समीक्षा के लिए उपलब्ध होगी। नमस्ते।",
}
_SKIP = {
    "i don't know",
    "i dont know",
    "don't know",
    "dont know",
    "not sure",
    "no",
    "nahi",
    "nahin",
    "नहीं",
    "नही",
    "i prefer not to answer",
    "prefer not to answer",
    "नहीं बताना चाहता",
    "नहीं बताना चाहती",
    "नहीं बताना चाहता हूँ",
    "नहीं बताना चाहती हूँ",
    "skip",
    "pata nahi",
    "pata nahin",
    "नहीं पता",
    "पता नहीं",
    "नही पता",
}


def _normalize(value: str) -> str:
    return re.sub(r"[^\w\u0900-\u097f']+", " ", value.lower()).strip()


class DetailWorkflow:
    """Preserve each answer as stated, without claiming it has been verified."""

    def __init__(self, language: LanguageChoice) -> None:
        if language not in _QUESTIONS:
            raise ValueError("A spoken agent language must be English or Hindi")
        self.language = language
        self.state = DetailState.ANSWER
        self._index = 0

    def begin(self) -> str:
        return _QUESTIONS[self.language][0]

    def handle_response(self, text: str, segment_id: str) -> DetailResult:
        if self.state == DetailState.COMPLETE:
            raise RuntimeError("Detail collection is complete")
        normalized = _normalize(text)
        if normalized in _SKIP:
            return self._advance()
        if not normalized:
            return DetailResult(_CORRECTION[self.language])
        result = self._advance()
        return DetailResult(result.prompt, _FIELDS[self._index - 1], text.strip(), segment_id)

    def _advance(self) -> DetailResult:
        self._index += 1
        if self._index == len(_FIELDS):
            self.state = DetailState.COMPLETE
            return DetailResult(_CLOSURE[self.language])
        self.state = DetailState.ANSWER
        return DetailResult(_QUESTIONS[self.language][self._index])

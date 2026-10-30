"""Consent gate that prevents intake collection before explicit caller approval."""

import re
from dataclasses import dataclass
from enum import StrEnum

from app.schemas.intake_call import LanguageChoice


class ConsentState(StrEnum):
    NOT_STARTED = "not_started"
    AWAITING_CONSENT = "awaiting_consent"
    GRANTED = "granted"
    DECLINED = "declined"
    CLOSED = "closed"


class ConsentAction(StrEnum):
    REQUEST_CONSENT = "request_consent"
    REPROMPT_CONSENT = "reprompt_consent"
    CONSENT_GRANTED = "consent_granted"
    CONSENT_DECLINED = "consent_declined"
    CLOSE_CALL = "close_call"


_AFFIRMATIVE_TERMS = {
    "yes",
    "yeah",
    "yep",
    "i consent",
    "i agree",
    "haan",
    "han",
    "ha",
    "ji haan",
    "haan ji",
    "हाँ",
    "हां",
    "जी हाँ",
    "हाँ जी",
    "सहमत",
    "अनुमति",
}
_DECLINED_TERMS = {
    "no",
    "nope",
    "nah",
    "i do not consent",
    "i don't consent",
    "nahi",
    "nahin",
    "na",
    "नहीं",
    "नही",
    "ना",
}

ENGLISH_CONSENT_PROMPT = (
    "Hello, and thank you for calling Victus Family Law Firm. I'm an AI assistant, and I'm here "
    "to listen and note down a few details so one of our lawyers can follow up with you. "
    "I cannnot give legal advice and speaking with me doesn't create a lawyer-client relationship."
    "With your permission, I'd also like to record this conversation so our team"
    "can review it. Are you comfortable continuing?"
)
ENGLISH_CONSENT_REPROMPT = (
    "Sorry, I didn't quite catch that. Are you comfortable with me recording this "
    "conversation and continuing? A simple yes or no is fine."
)
ENGLISH_CONSENT_GRANTED_RESPONSE = "Thank you."
ENGLISH_CONSENT_DECLINED_RESPONSE = (
    "That's completely fine. I won't record this call or collect any details. "
    "Thank you for calling, and take care."
)
ENGLISH_CONSENT_UNCLEAR_CLOSURE = (
    "I wasn't able to confirm, so I won't record this call or collect any details. "
    "You're welcome to call back anytime. Take care."
)


@dataclass(frozen=True)
class ConsentDialogue:
    language_code: str
    prompt: str
    reprompt: str
    granted_response: str
    declined_response: str
    unclear_closure: str


HINDI_CONSENT_DIALOGUE = ConsentDialogue(
    language_code="hi-IN",
    prompt=(
        "मैं लॉ फर्म की एआई सहायक हूँ। आपकी शुरुआती जानकारी वकील की समीक्षा के लिए ले सकती हूँ। "
        "कानूनी सलाह नहीं दे सकती, और इस कॉल से वकील और मुवक्किल का रिश्ता नहीं बनता। "
        "आपकी सहमति से यह बातचीत रिकॉर्ड की जा सकती है और फर्म इसे सुन सकती है। "
        "क्या हम आगे बढ़ें? कृपया हाँ या नहीं कहें।"
    ),
    reprompt="क्या हम आगे बढ़ें? कृपया हाँ या नहीं कहें।",
    granted_response="धन्यवाद।",
    declined_response="मैं समझती हूँ। मैं मामले की जानकारी एकत्र नहीं करूँगी। कॉल करने के लिए धन्यवाद।",
    unclear_closure=(
        "मैं आपकी सहमति की पुष्टि नहीं कर सकी, इसलिए मैं मामले की जानकारी एकत्र नहीं करूँगी। "
        "कॉल करने के लिए धन्यवाद।"
    ),
)

ENGLISH_CONSENT_DIALOGUE = ConsentDialogue(
    language_code="en-IN",
    prompt=ENGLISH_CONSENT_PROMPT,
    reprompt=ENGLISH_CONSENT_REPROMPT,
    granted_response=ENGLISH_CONSENT_GRANTED_RESPONSE,
    declined_response=ENGLISH_CONSENT_DECLINED_RESPONSE,
    unclear_closure=ENGLISH_CONSENT_UNCLEAR_CLOSURE,
)


def get_consent_dialogue(language: LanguageChoice) -> ConsentDialogue:
    """Return the consent copy and TTS language for the caller's explicit choice."""
    return {
        LanguageChoice.ENGLISH: ENGLISH_CONSENT_DIALOGUE,
        LanguageChoice.HINDI: HINDI_CONSENT_DIALOGUE,
    }[language]


class ConsentWorkflow:
    """Accept only an explicit affirmative answer before allowing case-fact collection."""

    def __init__(self) -> None:
        self._state = ConsentState.NOT_STARTED
        self._unclear_responses = 0

    @property
    def state(self) -> ConsentState:
        return self._state

    @property
    def allows_fact_collection(self) -> bool:
        return self._state == ConsentState.GRANTED

    def begin(self) -> ConsentAction:
        if self._state != ConsentState.NOT_STARTED:
            raise RuntimeError("Consent workflow has already started")
        self._state = ConsentState.AWAITING_CONSENT
        return ConsentAction.REQUEST_CONSENT

    def handle_response(self, transcript: str) -> ConsentAction:
        if self._state != ConsentState.AWAITING_CONSENT:
            raise RuntimeError("Consent response is not expected in the current state")

        if _contains_term(transcript, _DECLINED_TERMS):
            self._state = ConsentState.DECLINED
            return ConsentAction.CONSENT_DECLINED
        if _contains_term(transcript, _AFFIRMATIVE_TERMS):
            self._state = ConsentState.GRANTED
            return ConsentAction.CONSENT_GRANTED

        self._unclear_responses += 1
        if self._unclear_responses == 1:
            return ConsentAction.REPROMPT_CONSENT
        self._state = ConsentState.CLOSED
        return ConsentAction.CLOSE_CALL

    def close(self) -> ConsentAction:
        if self._state in {ConsentState.GRANTED, ConsentState.DECLINED, ConsentState.CLOSED}:
            return ConsentAction.CLOSE_CALL
        self._state = ConsentState.CLOSED
        return ConsentAction.CLOSE_CALL


def _contains_term(transcript: str, terms: set[str]) -> bool:
    tokens = re.findall(r"[\u0900-\u097F]+|[\w']+", transcript.lower())
    return any(_contains_token_sequence(tokens, term.split()) for term in terms)


def _contains_token_sequence(tokens: list[str], term_tokens: list[str]) -> bool:
    sequence_length = len(term_tokens)
    return any(
        tokens[index : index + sequence_length] == term_tokens
        for index in range(len(tokens) - sequence_length + 1)
    )

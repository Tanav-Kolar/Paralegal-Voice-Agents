"""Consent gate that prevents intake collection before explicit caller approval."""

import re
from enum import StrEnum


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
    "Hello. I am an AI intake assistant for the law firm. I can collect initial information "
    "for a lawyer to review, but I cannot give legal advice, and this call does not create a "
    "lawyer-client relationship. If you consent, this conversation may be recorded and reviewed "
    "by the firm. Do you consent to continue? Please say yes or no."
)
ENGLISH_CONSENT_REPROMPT = "Please answer yes or no. Do you consent to continue?"
ENGLISH_CONSENT_GRANTED_RESPONSE = (
    "Thank you. I have your consent. The intake workflow will now continue."
)
ENGLISH_CONSENT_DECLINED_RESPONSE = (
    "I understand. I will not collect case information. Thank you for calling. Goodbye."
)
ENGLISH_CONSENT_UNCLEAR_CLOSURE = (
    "I was unable to confirm your consent, so I will not collect case information. "
    "Thank you for calling. Goodbye."
)


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

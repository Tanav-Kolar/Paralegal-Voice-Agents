"""Language-selection gate for the consent-first voice experience."""

import re
from enum import StrEnum

from app.schemas.intake_call import LanguageChoice


class LanguageSelectionState(StrEnum):
    NOT_STARTED = "not_started"
    AWAITING_SELECTION = "awaiting_selection"
    SELECTED = "selected"
    CLOSED = "closed"


class LanguageSelectionAction(StrEnum):
    REQUEST_LANGUAGE = "request_language"
    REPROMPT_LANGUAGE = "reprompt_language"
    LANGUAGE_SELECTED = "language_selected"
    CLOSE_CALL = "close_call"


_LANGUAGE_TERMS = {
    LanguageChoice.ENGLISH: {"english", "angrezi", "अंग्रेज़ी", "अंग्रेजी"},
    LanguageChoice.HINDI: {"hindi", "हिंदी", "हिन्दी"},
}


class LanguageSelectionWorkflow:
    """Require a clear language choice before presenting consent disclosures."""

    def __init__(self) -> None:
        self._state = LanguageSelectionState.NOT_STARTED
        self._unclear_responses = 0
        self._selected_language: LanguageChoice | None = None

    @property
    def state(self) -> LanguageSelectionState:
        return self._state

    @property
    def selected_language(self) -> LanguageChoice | None:
        return self._selected_language

    def begin(self) -> LanguageSelectionAction:
        if self._state != LanguageSelectionState.NOT_STARTED:
            raise RuntimeError("Language selection has already started")
        self._state = LanguageSelectionState.AWAITING_SELECTION
        return LanguageSelectionAction.REQUEST_LANGUAGE

    def handle_response(self, transcript: str) -> LanguageSelectionAction:
        if self._state != LanguageSelectionState.AWAITING_SELECTION:
            raise RuntimeError("Language selection is not expected in the current state")

        matches = [
            language
            for language, terms in _LANGUAGE_TERMS.items()
            if _contains_term(transcript, terms)
        ]
        if len(matches) == 1:
            self._selected_language = matches[0]
            self._state = LanguageSelectionState.SELECTED
            return LanguageSelectionAction.LANGUAGE_SELECTED

        self._unclear_responses += 1
        if self._unclear_responses == 1:
            return LanguageSelectionAction.REPROMPT_LANGUAGE
        self._state = LanguageSelectionState.CLOSED
        return LanguageSelectionAction.CLOSE_CALL


def _contains_term(transcript: str, terms: set[str]) -> bool:
    tokens = re.findall(r"[\u0900-\u097F]+|[\w']+", transcript.lower())
    return any(_contains_token_sequence(tokens, term.split()) for term in terms)


def _contains_token_sequence(tokens: list[str], term_tokens: list[str]) -> bool:
    sequence_length = len(term_tokens)
    return any(
        tokens[index : index + sequence_length] == term_tokens
        for index in range(len(tokens) - sequence_length + 1)
    )

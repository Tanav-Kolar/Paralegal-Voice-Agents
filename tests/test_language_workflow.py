import pytest

from app.schemas.intake_call import LanguageChoice
from app.workflows.language import (
    LanguageSelectionAction,
    LanguageSelectionState,
    LanguageSelectionWorkflow,
)


@pytest.mark.parametrize(
    ("response", "language"),
    [
        ("English please", LanguageChoice.ENGLISH),
        ("हिंदी", LanguageChoice.HINDI),
        ("Hindi please, thoda mix karunga", LanguageChoice.HINDI),
    ],
)
def test_language_selection_accepts_supported_languages(
    response: str, language: LanguageChoice
) -> None:
    workflow = LanguageSelectionWorkflow()

    assert workflow.begin() == LanguageSelectionAction.REQUEST_LANGUAGE
    assert workflow.handle_response(response) == LanguageSelectionAction.LANGUAGE_SELECTED
    assert workflow.selected_language == language
    assert workflow.state == LanguageSelectionState.SELECTED


def test_language_selection_reprompts_once_then_closes() -> None:
    workflow = LanguageSelectionWorkflow()
    workflow.begin()

    assert workflow.handle_response("What languages do you support?") == (
        LanguageSelectionAction.REPROMPT_LANGUAGE
    )
    assert workflow.handle_response("I do not know") == LanguageSelectionAction.CLOSE_CALL
    assert workflow.state == LanguageSelectionState.CLOSED


def test_language_selection_rejects_ambiguous_choice() -> None:
    workflow = LanguageSelectionWorkflow()
    workflow.begin()

    assert workflow.handle_response("English or Hindi") == LanguageSelectionAction.REPROMPT_LANGUAGE

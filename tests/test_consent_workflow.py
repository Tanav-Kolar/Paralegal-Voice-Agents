import pytest

from app.workflows.consent import ConsentAction, ConsentState, ConsentWorkflow


@pytest.mark.parametrize("response", ["Yes, I consent.", "Haan ji", "हाँ, मैं सहमत हूँ"])
def test_explicit_affirmative_consent_allows_fact_collection(response: str) -> None:
    workflow = ConsentWorkflow()

    assert workflow.begin() == ConsentAction.REQUEST_CONSENT
    assert workflow.handle_response(response) == ConsentAction.CONSENT_GRANTED
    assert workflow.state == ConsentState.GRANTED
    assert workflow.allows_fact_collection is True


@pytest.mark.parametrize("response", ["No", "Nahi", "नहीं"])
def test_declined_consent_blocks_fact_collection(response: str) -> None:
    workflow = ConsentWorkflow()
    workflow.begin()

    assert workflow.handle_response(response) == ConsentAction.CONSENT_DECLINED
    assert workflow.state == ConsentState.DECLINED
    assert workflow.allows_fact_collection is False


def test_unclear_consent_is_reprompted_once_then_closed() -> None:
    workflow = ConsentWorkflow()
    workflow.begin()

    assert workflow.handle_response("What is this about?") == ConsentAction.REPROMPT_CONSENT
    assert workflow.allows_fact_collection is False
    assert workflow.handle_response("Can you explain?") == ConsentAction.CLOSE_CALL
    assert workflow.state == ConsentState.CLOSED


def test_consent_cannot_be_reused_after_a_decision() -> None:
    workflow = ConsentWorkflow()
    workflow.begin()
    workflow.handle_response("Yes")

    with pytest.raises(RuntimeError, match="not expected"):
        workflow.handle_response("I want a divorce")

import pytest

from app.schemas.intake_call import LanguageChoice
from app.workflows.details import DetailState, DetailWorkflow


@pytest.mark.parametrize("agent_language", [LanguageChoice.ENGLISH, LanguageChoice.HINDI])
def test_answers_advance_without_repetition_and_remain_as_stated(agent_language):
    workflow = DetailWorkflow(agent_language)
    workflow.begin()
    answer = "Mera naam नमिता hai"

    result = workflow.handle_response(answer, "source-1")
    assert result.field == "caller_name_as_stated"
    assert result.value == answer
    assert result.source_segment_id == "source-1"
    assert answer not in result.prompt
    assert workflow.handle_response("Delhi", "source-2").field == "location_as_stated"
    assert workflow.handle_response("15 March 2024", "source-3").field == "relevant_date_as_stated"
    assert workflow.state == DetailState.COMPLETE


def test_unavailable_details_are_not_saved_as_facts():
    workflow = DetailWorkflow(LanguageChoice.ENGLISH)
    workflow.begin()
    assert workflow.handle_response("prefer not to answer", "skip-1").field is None
    assert workflow.handle_response("pata nahi", "skip-2").field is None
    assert workflow.handle_response("I don't know", "skip-3").field is None
    assert workflow.state == DetailState.COMPLETE
    with pytest.raises(RuntimeError):
        workflow.handle_response("extra", "extra")

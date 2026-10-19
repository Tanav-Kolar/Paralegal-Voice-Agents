import pytest

from app.schemas.intake_call import LanguageChoice
from app.workflows.conversation import (
    ConversationReply,
    ConversationUnavailable,
    ConversationWorkflow,
)


class FakeProvider:
    def __init__(self) -> None:
        self.requests = []

    async def reply(self, messages):
        self.requests.append(messages)
        return ConversationReply(reply="Tell me more.", finished=len(self.requests) == 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("language", [LanguageChoice.ENGLISH, LanguageChoice.HINDI])
async def test_conversation_retains_mixed_language_context_and_fixed_agent_language(language):
    provider = FakeProvider()
    workflow = ConversationWorkflow(language, provider)
    await workflow.begin()
    await workflow.handle_response("Mera naam नमिता hai")
    result = await workflow.handle_response("We separated last year")

    messages = provider.requests[-1]
    assert ("Speak in English" if language == LanguageChoice.ENGLISH else "Speak in Hindi") in (
        messages[0]["content"]
    )
    assert messages[-3:] == [
        {"role": "user", "content": "Mera naam नमिता hai"},
        {"role": "assistant", "content": "Tell me more."},
        {"role": "user", "content": "We separated last year"},
    ]
    assert result.finished and workflow.finished
    with pytest.raises(RuntimeError):
        await workflow.handle_response("extra")


@pytest.mark.asyncio
async def test_conversation_rejects_model_closing_at_start():
    class PrematureEnd:
        async def reply(self, messages):
            return ConversationReply(reply="Goodbye", finished=True)

    with pytest.raises(ConversationUnavailable):
        await ConversationWorkflow(LanguageChoice.ENGLISH, PrematureEnd()).begin()

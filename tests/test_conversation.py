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


@pytest.mark.asyncio
async def test_conversation_does_not_speak_a_name_the_caller_never_supplied():
    class GuessingProvider:
        async def reply(self, messages):
            if len(messages) == 2:
                return ConversationReply(reply="What brought you to the firm?", finished=False)
            return ConversationReply(
                reply="Is your husband's name Rohan Mehta?", finished=False
            )

    workflow = ConversationWorkflow(LanguageChoice.ENGLISH, GuessingProvider())
    await workflow.begin()
    response = await workflow.handle_response("I want to discuss my separation.")

    assert response.reply == "What else would you like the lawyer to know?"
    assert "Rohan" not in str(workflow._messages)


@pytest.mark.asyncio
async def test_conversation_can_refer_to_a_name_the_caller_supplied():
    class Provider:
        async def reply(self, messages):
            if len(messages) == 2:
                return ConversationReply(reply="What brought you to the firm?", finished=False)
            return ConversationReply(
                reply="When did you last speak with Rohan Mehta?", finished=False
            )

    workflow = ConversationWorkflow(LanguageChoice.ENGLISH, Provider())
    await workflow.begin()
    response = await workflow.handle_response("My husband's name is Rohan Mehta.")

    assert response.reply == "When did you last speak with Rohan Mehta?"


@pytest.mark.asyncio
async def test_conversation_first_question_cannot_introduce_a_case_detail():
    class GuessingProvider:
        async def reply(self, messages):
            return ConversationReply(reply="Are you calling about Rohan Mehta?", finished=False)

    workflow = ConversationWorkflow(LanguageChoice.ENGLISH, GuessingProvider())
    response = await workflow.begin()

    assert response.reply == "What brought you to the firm?"

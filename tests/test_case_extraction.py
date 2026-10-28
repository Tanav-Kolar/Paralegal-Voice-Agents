from uuid import UUID

import pytest
from cryptography.fernet import Fernet

from app.security.field_encryption import FieldEncryptor
from app.services.brief import triage
from app.services.case_taxonomy import matter_type_from_call
from app.services.extraction import extract_case


@pytest.mark.asyncio
async def test_extract_case_requires_caller_evidence_for_summary_and_category() -> None:
    caller_id, agent_id = UUID(int=1), UUID(int=2)

    async def complete(*_):
        return {
            "matter_type": "Divorce and separation",
            "matter_lines": [1, 2],
            "summary": "The parties separated in March. The caller seeks divorce advice.",
            "summary_lines": [1],
            "facts": [
                {"field": "Timeline of events", "value": "Separated in March", "lines": [1]},
                {"field": "Children and care", "value": "One child", "lines": [2]},
            ],
        }

    result = await extract_case(
        complete,
        [
            (caller_id, "caller", "We separated in March. I want a divorce."),
            (agent_id, "agent", "Do you have one child?"),
        ],
    )
    assert result.matter_type == "Divorce and separation"
    assert result.summary == "The parties separated in March. The caller seeks divorce advice."
    assert next(f for f in result.facts if f.field_name == "Timeline of events").value == (
        "Separated in March"
    )
    assert next(f for f in result.facts if f.field_name == "Children and care").not_provided


def test_triage_uses_saved_third_person_summary_and_standard_category() -> None:
    encryptor = FieldEncryptor(Fernet.generate_key().decode())
    document = {
        "intake": {
            "matter_type": "Divorce and separation",
            "triage_summary_encrypted": encryptor.encrypt(
                "The parties separated in March. The caller seeks divorce advice."
            ),
            "transcript_segments": [
                {"speaker": "caller", "text_encrypted": encryptor.encrypt("I want a divorce")}
            ],
        }
    }
    summary = triage(document, encryptor)
    assert summary["overview"].startswith("The parties separated")
    assert summary["matterType"] == "Divorce and separation"
    assert summary["overview"] != summary["callerStatements"][0]["text"]


def test_legacy_classification_prefers_call_content_over_generic_label() -> None:
    assert matter_type_from_call("family", None, ["My father died and left an estate."]) == (
        "Inheritance and succession"
    )
    assert matter_type_from_call(None, None, ["I will call tomorrow."]) is None

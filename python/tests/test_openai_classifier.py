import sys
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter

from sentinel.nodes.llm_classify import TransientProviderError, interpret_incident
from sentinel.openai_classifier import (
    CLASSIFICATION_SYSTEM_PROMPT,
    OpenAIClassificationModel,
    create_openai_classifier,
)
from sentinel.schemas import classifySchema


def valid_response():
    return {
        "recommended_severity": "sev2",
        "symptoms": ["high_latency"],
        "rationale": "Some requests time out.",
        "affected_components": ["api"],
        "reported_impact": ["Some requests time out."],
        "source_references": ["Some requests time out"],
        "uncertainties": ["Affected-user count is unknown."],
    }


class FakeChat:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = []

    def invoke(self, messages):
        self.messages.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_adapter_sends_grounded_system_prompt_and_incident_only():
    raw = SimpleNamespace(usage_metadata={"input_tokens": 21, "output_tokens": 12})
    chat = FakeChat([{"parsed": valid_response(), "raw": raw}])
    adapter = OpenAIClassificationModel(chat)
    result = interpret_incident("Some requests time out", adapter)
    assert result == valid_response()
    assert chat.messages == [
        [
            {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
            {"role": "user", "content": "Some requests time out"},
        ]
    ]
    assert "exact contiguous quote" in CLASSIFICATION_SYSTEM_PROMPT
    assert "affected-user counts" in CLASSIFICATION_SYSTEM_PROMPT
    assert adapter.token_usage == {
        "input_tokens": 21,
        "output_tokens": 12,
        "calls_with_usage": 1,
    }


def test_malformed_provider_response_reaches_one_grounded_repair():
    chat = FakeChat(
        [
            {"parsed": None, "raw": SimpleNamespace(content="not JSON")},
            {"parsed": valid_response(), "raw": None},
        ]
    )
    result = interpret_incident(
        "Some requests time out", OpenAIClassificationModel(chat)
    )
    assert result == valid_response()
    assert len(chat.messages) == 2
    assert (
        "Original incident description: Some requests time out"
        in chat.messages[1][1]["content"]
    )


@pytest.mark.parametrize(
    ("status", "reason"),
    [(429, "rate_limited"), (503, "provider_unavailable")],
)
def test_adapter_translates_temporary_http_failure(status, reason):
    class StatusError(Exception):
        status_code = status

    chat = FakeChat([StatusError("temporary failure")])
    with pytest.raises(TransientProviderError) as error:
        OpenAIClassificationModel(chat).invoke("incident")
    assert error.value.reason == reason


def test_adapter_leaves_permanent_http_failure_for_nonretry_fallback():
    class StatusError(Exception):
        status_code = 401

    chat = FakeChat([StatusError("bad credentials")])
    with pytest.raises(StatusError):
        OpenAIClassificationModel(chat).invoke("incident")


def test_factory_requires_configuration_without_creating_a_client(monkeypatch):
    monkeypatch.delenv("SENTINEL_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="SENTINEL_OPENAI_MODEL"):
        create_openai_classifier()


def test_factory_configures_structured_output_and_disables_hidden_retries(monkeypatch):
    created = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            created.append(("chat", kwargs))

        def with_structured_output(self, schema, **kwargs):
            created.append(("structured", schema, kwargs))
            return FakeChat([{"parsed": valid_response(), "raw": None}])

    monkeypatch.setenv("SENTINEL_OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    monkeypatch.setitem(
        sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=FakeChatOpenAI)
    )
    adapter = create_openai_classifier()
    assert isinstance(adapter, OpenAIClassificationModel)
    assert created == [
        ("chat", {"model": "test-model", "timeout": 30, "max_retries": 0}),
        (
            "structured",
            TypeAdapter(classifySchema).json_schema(),
            {"method": "json_schema", "include_raw": True, "strict": False},
        ),
    ]


def test_installed_langchain_adapter_initializes_without_api_call(monkeypatch):
    pytest.importorskip("langchain_openai")
    monkeypatch.setenv("SENTINEL_OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    assert isinstance(create_openai_classifier(), OpenAIClassificationModel)

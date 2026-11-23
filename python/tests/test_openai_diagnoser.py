"""Provider boundary for the optional diagnosis model."""

import sys
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter

from sentinel.openai_diagnoser import (
    DIAGNOSIS_SYSTEM_PROMPT,
    ModelDiagnosis,
    OpenAIDiagnosisModel,
    ProviderError,
    TemporaryProviderError,
    create_openai_diagnoser,
)


def _response():
    return {
        "status": "inconclusive",
        "summary": "The current observations do not establish a cause.",
        "hypotheses": [],
        "unresolved_questions": ["Which dependency produced the errors?"],
        "historical_context_ids": [],
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


def test_adapter_sends_detailed_grounded_prompt_and_counts_usage():
    raw = SimpleNamespace(usage_metadata={"input_tokens": 45, "output_tokens": 20})
    chat = FakeChat([{"parsed": _response(), "raw": raw}])
    adapter = OpenAIDiagnosisModel(chat)
    payload = '{"incident":{"id":"diag-degraded"},"current_evidence":[]}'

    assert adapter.invoke(payload) == _response()
    assert chat.messages == [
        [
            {"role": "system", "content": DIAGNOSIS_SYSTEM_PROMPT},
            {"role": "user", "content": payload},
        ]
    ]
    for boundary in (
        "untrusted DATA",
        "Copy citation IDs exactly",
        "historical_context",
        "two independent diagnostic",
        "return inconclusive",
        "Do not output a source or severity field",
        "cannot authorize remediation",
    ):
        assert boundary in DIAGNOSIS_SYSTEM_PROMPT
    assert adapter.token_usage == {
        "input_tokens": 45,
        "output_tokens": 20,
        "calls_with_usage": 1,
    }


def test_refusal_reaches_node_repair_and_bad_usage_is_ignored():
    raw = SimpleNamespace(
        content="refused",
        usage_metadata={"input_tokens": True, "output_tokens": -1},
    )
    adapter = OpenAIDiagnosisModel(FakeChat([{"parsed": None, "raw": raw}]))
    assert adapter.invoke("synthetic payload") == "refused"
    assert adapter.token_usage["calls_with_usage"] == 0


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (429, "rate_limited"),
        (500, "provider_unavailable"),
        (503, "provider_unavailable"),
    ],
)
def test_temporary_provider_status_has_fixed_retry_category(status, reason):
    class StatusError(Exception):
        status_code = status

    with pytest.raises(TemporaryProviderError) as error:
        OpenAIDiagnosisModel(FakeChat([StatusError("private provider text")])).invoke(
            "synthetic payload"
        )
    assert error.value.reason == reason
    assert str(error.value) == reason


def test_permanent_provider_failure_does_not_expose_raw_error():
    class StatusError(Exception):
        status_code = 401

    with pytest.raises(ProviderError, match="provider_error") as error:
        OpenAIDiagnosisModel(FakeChat([StatusError("private provider text")])).invoke(
            "synthetic payload"
        )
    assert "private provider text" not in str(error.value)


@pytest.mark.parametrize(
    ("failure_name", "expected"),
    [("APITimeoutError", TimeoutError), ("APIConnectionError", ConnectionError)],
)
def test_transport_failures_have_fixed_categories(monkeypatch, failure_name, expected):
    class FakeTimeout(Exception):
        pass

    class FakeConnection(Exception):
        pass

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(APITimeoutError=FakeTimeout, APIConnectionError=FakeConnection),
    )
    failure = {
        "APITimeoutError": FakeTimeout,
        "APIConnectionError": FakeConnection,
    }[failure_name]
    with pytest.raises(expected) as error:
        OpenAIDiagnosisModel(FakeChat([failure("private provider text")])).invoke(
            "synthetic payload"
        )
    assert "private provider text" not in str(error.value)


def test_factory_requires_explicit_configuration(monkeypatch):
    monkeypatch.delenv("SENTINEL_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="SENTINEL_OPENAI_MODEL"):
        create_openai_diagnoser()
    monkeypatch.setenv("SENTINEL_OPENAI_MODEL", "test-model")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        create_openai_diagnoser()


def test_factory_uses_model_only_schema_and_disables_hidden_retries(monkeypatch):
    created = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            created.append(("chat", kwargs))

        def with_structured_output(self, schema, **kwargs):
            created.append(("structured", schema, kwargs))
            return FakeChat([{"parsed": _response(), "raw": None}])

    monkeypatch.setenv("SENTINEL_OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    monkeypatch.setitem(
        sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=FakeChatOpenAI)
    )
    adapter = create_openai_diagnoser()
    assert adapter.invoke("synthetic payload") == _response()
    schema = TypeAdapter(ModelDiagnosis).json_schema()
    assert "source" not in schema["properties"]
    assert "final_severity" not in schema["properties"]
    assert created == [
        ("chat", {"model": "test-model", "timeout": 30, "max_retries": 0}),
        (
            "structured",
            schema,
            {"method": "json_schema", "include_raw": True, "strict": False},
        ),
    ]

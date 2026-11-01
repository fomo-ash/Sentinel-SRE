"""Provider-neutral model interpretation with bounded deterministic fallback."""

import json
import time
from collections.abc import Callable
from typing import Protocol

from pydantic import TypeAdapter, ValidationError

from sentinel.nodes.classify import classify
from sentinel.schemas import (
    ClassificationFailure,
    InvestigationState,
    classifySchema,
)

_classification_adapter = TypeAdapter(classifySchema)


class ClassificationModel(Protocol):
    """Interface implemented by fake models and future provider adapters."""

    def invoke(self, description: str) -> object: ...


class TransientProviderError(Exception):
    """Provider adapter signal for a temporary failure such as rate limiting."""

    def __init__(self, reason: ClassificationFailure = "provider_unavailable"):
        self.reason = reason
        super().__init__(reason)


def _failure_reason(error: Exception) -> ClassificationFailure:
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ConnectionError):
        return "provider_unavailable"
    if isinstance(error, TransientProviderError):
        return error.reason
    return "provider_error"


def invoke_with_retry(
    model: ClassificationModel,
    prompt: str,
    *,
    max_attempts: int = 3,
    base_delay: float = 0.1,
    sleep: Callable[[float], None] = time.sleep,
) -> object:
    """Retry temporary failures only, with a fixed maximum call count."""
    if max_attempts < 1 or base_delay < 0:
        raise ValueError("max_attempts must be positive and base_delay nonnegative")

    # This budget covers one provider call; schema repair has its own limit.
    for attempt in range(max_attempts):
        try:
            return model.invoke(prompt)
        except (TimeoutError, ConnectionError, TransientProviderError):
            if attempt + 1 == max_attempts:
                raise
            sleep(base_delay * 2**attempt)
    raise AssertionError("retry loop exhausted without returning or raising")


class _RetryingModel:
    def __init__(
        self,
        model: ClassificationModel,
        max_attempts: int,
        base_delay: float,
        sleep: Callable[[float], None],
    ):
        self.model = model
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.sleep = sleep

    def invoke(self, prompt: str) -> object:
        return invoke_with_retry(
            self.model,
            prompt,
            max_attempts=self.max_attempts,
            base_delay=self.base_delay,
            sleep=self.sleep,
        )


def interpret_incident(description: str, model: ClassificationModel) -> classifySchema:
    """Validate output and make one grounded repair call for schema errors."""
    raw = model.invoke(description)
    try:
        return _classification_adapter.validate_python(raw)
    except ValidationError as error:
        # Include the original report so a repair cannot rely on malformed output alone.
        repair_request = (
            "Return a corrected structured classification for the original incident. "
            "Use only facts supported by its description; do not invent metrics or "
            "affected-user counts. Return every required field in this JSON schema: "
            f"{json.dumps(_classification_adapter.json_schema())}\n"
            f"Original incident description: {description}\n"
            f"Validation errors: {json.dumps(error.errors(include_input=False))}"
        )
        repaired = model.invoke(repair_request)
        return _classification_adapter.validate_python(repaired)


def build_llm_classify_node(
    model: ClassificationModel,
    *,
    max_attempts: int = 3,
    base_delay: float = 0.1,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[InvestigationState], dict]:
    """Build a classification node that falls back to the baseline on failure."""
    if max_attempts < 1 or base_delay < 0:
        raise ValueError("max_attempts must be positive and base_delay nonnegative")
    retrying_model = _RetryingModel(model, max_attempts, base_delay, sleep)

    def llm_classify(state: InvestigationState) -> dict:
        try:
            interpretation = interpret_incident(
                state["incident"]["summary"], retrying_model
            )
        except ValidationError:
            reason: ClassificationFailure = "invalid_output"
        except (TimeoutError, ConnectionError, TransientProviderError) as error:
            reason = _failure_reason(error)
        except Exception:
            reason = "provider_error"
        else:
            # Keep interpretation separate; the policy node will choose final severity.
            return {
                "model_interpretation": interpretation,
                "symptoms": interpretation["symptoms"],
                "classification_source": "llm",
                "failure_reason": None,
            }

        # Fallback leaves ambiguous impact as "unknown" when rules cannot tell.
        return {
            **classify(state),
            "model_interpretation": None,
            "classification_source": "baseline_fallback",
            "failure_reason": reason,
        }

    return llm_classify

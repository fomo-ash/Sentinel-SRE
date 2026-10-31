"""Provider-neutral model interpretation; severity policy is a separate step."""

import json
from typing import Protocol

from pydantic import TypeAdapter, ValidationError

from sentinel.schemas import classifySchema

_classification_adapter = TypeAdapter(classifySchema)


class ClassificationModel(Protocol):
    """Minimal interface implemented by a fake or a future real model adapter."""

    def invoke(self, description: str) -> dict: ...


def interpret_incident(description: str, model: ClassificationModel) -> classifySchema:
    """Validate model output, with one repair attempt for schema errors."""
    raw = model.invoke(description)
    try:
        return _classification_adapter.validate_python(raw)
    except ValidationError as error:
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

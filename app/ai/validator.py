"""Validate AI JSON output."""

import json
from typing import Any

from pydantic import ValidationError

from app.ai.schemas import CfoAiOutput


def validate_cfo_output(data: dict[str, Any] | str) -> tuple[CfoAiOutput | None, str | None]:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON: {e}"
    try:
        return CfoAiOutput.model_validate(data), None
    except ValidationError as e:
        return None, str(e)

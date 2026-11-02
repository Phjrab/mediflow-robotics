from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SorterError(RuntimeError):
    code: str
    observed: Any
    required: str
    next_action: str

    def __str__(self) -> str:
        return self.code

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "FAILED",
            "error": self.code,
            "observed": self.observed,
            "required": self.required,
            "next_action": self.next_action,
        }

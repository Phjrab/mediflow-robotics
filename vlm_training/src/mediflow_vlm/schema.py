from __future__ import annotations

import json
from typing import Any


MEDICINE_MAP = {
    "medicine_a": "A",
    "medicine_b": "B",
    "medicine_c": "C",
}

ORIENTATION_MAP = {
    "upright": "upright",
    "fallen": "fallen",
    "tilted": "tilted",
    "not_visible": "unknown",
}

BIN_MAP = {
    "A": "BIN_A",
    "B": "BIN_B",
    "C": "BIN_C",
    "unknown": "NONE",
}

MEDICINE_VALUES = frozenset(BIN_MAP)
ORIENTATION_VALUES = frozenset({"upright", "fallen", "tilted", "unknown"})
BIN_VALUES = frozenset(BIN_MAP.values())
GRASP_REGION_VALUES = frozenset({"lid", "body", "unknown"})


COMPACT_PROMPT = (
    "이미지에서 로봇 그리퍼가 접근하거나 파지한 약통 하나를 판단하라. "
    "약통 종류, 자세, 접근 또는 파지 부위를 순서대로 답하라. "
    "첫 값은 A, B, C, unknown 중 하나, 둘째 값은 upright, fallen, unknown 중 하나, "
    "셋째 값은 lid, body, unknown 중 하나여야 한다. "
    "설명 없이 medicine_id|orientation|grasp_region 형식의 값 세 개만 반환하라."
)

ACTION_PROMPT = (
    "이미지에서 로봇 그리퍼가 접근하거나 파지한 약통 하나를 판단하라. "
    "약통의 현재 자세와 그리퍼의 접근 또는 파지 부위만 순서대로 답하라. "
    "첫 값은 upright, fallen, unknown 중 하나, 둘째 값은 lid, body, unknown 중 하나여야 한다. "
    "설명 없이 orientation|grasp_region 형식의 값 두 개만 반환하라."
)

GRASP_PROMPT = (
    "이미지에서 로봇 그리퍼가 접근하거나 파지한 약통 하나를 판단하라. "
    "그리퍼의 접근 또는 파지 부위가 뚜껑이면 lid, 몸통이면 body, "
    "판단할 수 없으면 unknown으로 답하라. 설명 없이 값 하나만 반환하라."
)


def completion_from_source(answer: dict[str, Any]) -> dict[str, str]:
    """Map source labels to the deliberately narrow first-stage VLM schema."""
    medicine_id = MEDICINE_MAP.get(answer.get("target_class"), "unknown")
    orientation = ORIENTATION_MAP.get(answer.get("object_state"), "unknown")
    return {
        "medicine_id": medicine_id,
        "orientation": orientation,
        "target_bin": BIN_MAP[medicine_id],
    }


def validate_completion(
    value: Any, *, require_grasp_region: bool = False
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return False, ["prediction is not a JSON object"]

    expected = {"medicine_id", "orientation", "target_bin"}
    if require_grasp_region:
        expected.add("grasp_region")
    missing = expected - set(value)
    extra = set(value) - expected
    if missing:
        errors.append(f"missing keys: {sorted(missing)}")
    if extra:
        errors.append(f"unexpected keys: {sorted(extra)}")

    medicine_id = value.get("medicine_id")
    orientation = value.get("orientation")
    target_bin = value.get("target_bin")
    if medicine_id not in MEDICINE_VALUES:
        errors.append(f"invalid medicine_id: {medicine_id!r}")
    if orientation not in ORIENTATION_VALUES:
        errors.append(f"invalid orientation: {orientation!r}")
    if target_bin not in BIN_VALUES:
        errors.append(f"invalid target_bin: {target_bin!r}")
    if medicine_id in MEDICINE_VALUES and target_bin != BIN_MAP[medicine_id]:
        errors.append("medicine_id and target_bin mapping disagree")
    if require_grasp_region and value.get("grasp_region") not in GRASP_REGION_VALUES:
        errors.append(f"invalid grasp_region: {value.get('grasp_region')!r}")
    return not errors, errors


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse a model response while tolerating a single Markdown code fence."""
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(candidate[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model output is not a JSON object")
    return parsed


def format_compact_completion(value: dict[str, Any]) -> str:
    """Use only supervised labels so constant JSON syntax cannot dominate loss."""
    return "|".join(
        str(value[key]) for key in ("medicine_id", "orientation", "grasp_region")
    )


def extract_compact_completion(text: str) -> dict[str, str]:
    """Parse three ordered labels and derive the deterministic target bin."""
    parts = [part.strip() for part in text.strip().split("|")]
    if len(parts) != 3:
        raise ValueError("compact output must contain exactly three pipe-separated labels")
    medicine_id, orientation, grasp_region = parts
    value = {
        "medicine_id": medicine_id,
        "orientation": orientation,
        "target_bin": BIN_MAP.get(medicine_id, "NONE"),
        "grasp_region": grasp_region,
    }
    valid, errors = validate_completion(value, require_grasp_region=True)
    if not valid:
        raise ValueError("; ".join(errors))
    return value


def validate_action_completion(value: Any) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return False, ["prediction is not an object"]
    expected = {"orientation", "grasp_region"}
    missing = expected - set(value)
    extra = set(value) - expected
    if missing:
        errors.append(f"missing keys: {sorted(missing)}")
    if extra:
        errors.append(f"unexpected keys: {sorted(extra)}")
    if value.get("orientation") not in ORIENTATION_VALUES:
        errors.append(f"invalid orientation: {value.get('orientation')!r}")
    if value.get("grasp_region") not in GRASP_REGION_VALUES:
        errors.append(f"invalid grasp_region: {value.get('grasp_region')!r}")
    return not errors, errors


def format_action_completion(value: dict[str, Any]) -> str:
    return "|".join(str(value[key]) for key in ("orientation", "grasp_region"))


def extract_action_completion(text: str) -> dict[str, str]:
    parts = [part.strip() for part in text.strip().split("|")]
    if len(parts) != 2:
        raise ValueError("action output must contain exactly two pipe-separated labels")
    value = {"orientation": parts[0], "grasp_region": parts[1]}
    valid, errors = validate_action_completion(value)
    if not valid:
        raise ValueError("; ".join(errors))
    return value


def compose_command_action(
    medicine_id: str, action: dict[str, Any]
) -> dict[str, str]:
    """Combine the command-owned medicine with image-owned action labels."""
    if medicine_id not in MEDICINE_VALUES:
        raise ValueError(f"invalid command medicine_id: {medicine_id!r}")
    valid, errors = validate_action_completion(action)
    if not valid:
        raise ValueError("; ".join(errors))
    return {
        "medicine_id": medicine_id,
        "orientation": str(action["orientation"]),
        "target_bin": BIN_MAP[medicine_id],
        "grasp_region": str(action["grasp_region"]),
    }


def validate_grasp_completion(value: Any) -> tuple[bool, list[str]]:
    if not isinstance(value, dict):
        return False, ["prediction is not an object"]
    if set(value) != {"grasp_region"}:
        return False, ["grasp prediction must contain only grasp_region"]
    if value.get("grasp_region") not in GRASP_REGION_VALUES:
        return False, [f"invalid grasp_region: {value.get('grasp_region')!r}"]
    return True, []


def format_grasp_completion(value: dict[str, Any]) -> str:
    return str(value["grasp_region"])


def extract_grasp_completion(text: str) -> dict[str, str]:
    value = {"grasp_region": text.strip()}
    valid, errors = validate_grasp_completion(value)
    if not valid:
        raise ValueError("; ".join(errors))
    return value

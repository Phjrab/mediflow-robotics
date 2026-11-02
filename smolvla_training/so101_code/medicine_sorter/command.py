from __future__ import annotations

import re
from collections.abc import Mapping

from .errors import SorterError
from .models import BasketColor, BottleSize, SortCommand


SIZE_ALIASES = {
    BottleSize.LARGE: ("큰", "대형", "A약통", "A 약통"),
    BottleSize.MEDIUM: ("중간", "중형", "B약통", "B 약통"),
    BottleSize.SMALL: ("작은", "소형", "C약통", "C 약통"),
}
COLOR_ALIASES = {
    BasketColor.RED: ("빨간색", "빨간", "빨강"),
    BasketColor.GREEN: ("초록색", "초록", "녹색"),
    BasketColor.BLUE: ("파란색", "파란", "파랑"),
}
AMBIGUOUS_WORDS = ("그거", "저거", "이거", "저기", "여기", "거기")
ACTION_WORDS = ("넣어", "옮겨", "분류")


def _matches(text: str, aliases: Mapping[object, tuple[str, ...]]) -> list[object]:
    return [value for value, words in aliases.items() if any(word in text for word in words)]


def parse_sort_command(
    text: str,
    *,
    default_destinations: Mapping[BottleSize, BasketColor] | None = None,
) -> SortCommand:
    if not isinstance(text, str) or not text.strip():
        raise SorterError("empty_command", text, "약통 크기와 목적지 색이 포함된 명령",
                          "예: 큰 약통 하나를 빨간 바구니에 넣어")
    normalized = re.sub(r"[.!?。]", "", text).strip()
    if any(word in normalized for word in AMBIGUOUS_WORDS):
        raise SorterError("ambiguous_reference", normalized, "명시적인 약통 크기와 바구니 색",
                          "대상과 목적지를 직접 말씀해 주세요")
    if "약통" not in normalized or not any(word in normalized for word in ACTION_WORDS):
        raise SorterError("unsupported_action", normalized, "약통 하나를 바구니에 넣는 명령",
                          "지원 형식으로 다시 말씀해 주세요")

    sizes = _matches(normalized, SIZE_ALIASES)
    if len(sizes) != 1:
        code = "missing_size" if not sizes else "ambiguous_size"
        raise SorterError(code, [value.value for value in sizes], "large/medium/small 중 하나",
                          "큰·중간·작은 중 하나를 지정해 주세요")
    colors = _matches(normalized, COLOR_ALIASES)
    if len(colors) > 1:
        raise SorterError("ambiguous_destination", [value.value for value in colors],
                          "red/green/blue 중 하나", "바구니 색 하나만 지정해 주세요")

    quantity = 1
    if any(word in normalized for word in ("전부", "모두")):
        quantity = 0
    numeric = re.search(r"(?<![A-Za-z])([0-9]+)\s*(?:개|병)?", normalized)
    if numeric:
        quantity = int(numeric.group(1))
    elif any(word in normalized for word in ("두 개", "두개", "둘", "세 개", "세개", "셋")):
        quantity = 2
    if quantity != 1:
        raise SorterError("unsupported_quantity", quantity, "quantity=1",
                          "한 번에 약통 하나만 요청해 주세요")

    size = sizes[0]
    if colors:
        destination = colors[0]
    elif default_destinations and size in default_destinations:
        destination = default_destinations[size]
    else:
        raise SorterError("missing_destination", None, "red/green/blue 목적지",
                          "바구니 색을 지정해 주세요")
    return SortCommand("sort", "medicine_bottle", size, destination, 1, text)

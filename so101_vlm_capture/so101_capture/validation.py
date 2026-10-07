ALLOWED = {
    "target_class": {"medicine_a", "medicine_b", "medicine_c", "none", "multiple"},
    "object_state": {"upright", "fallen", "tilted", "occluded", "not_visible", "mixed"},
    "visibility": {"visible", "partially_visible", "not_visible"},
    "graspability": {"graspable", "difficult", "unsafe", "unknown"},
    "destination": {"basket_1", "basket_2", "basket_3", "none"},
    "destination_visibility": {"visible", "partially_visible", "not_visible"},
    "grasp_strategy": {"lid_grasp", "body_grasp", "rescan", "stop"},
    "capture_phase": {"before_grasp", "grasping", "after_grasp", "after_place"},
    "action_result": {"not_recorded", "running", "success", "failure", "human_intervention"},
    "required_skill": {"pick_upright", "pick_fallen", "stop_and_request_help"},
    "instruction": {
        "medicine_a를 알맞은 바구니에 넣어",
        "medicine_b를 알맞은 바구니에 넣어",
        "medicine_c를 알맞은 바구니에 넣어",
    },
}


def validate_labels(payload):
    errors = []
    for field, choices in ALLOWED.items():
        if payload.get(field) not in choices:
            errors.append(f"{field}: 허용되지 않은 값입니다.")
    target = payload.get("target_class")
    state = payload.get("object_state")
    visibility = payload.get("visibility")
    graspability = payload.get("graspability")
    destination = payload.get("destination")
    destination_visibility = payload.get("destination_visibility")
    strategy = payload.get("grasp_strategy")
    skill = payload.get("required_skill")
    if (state == "not_visible" or visibility == "not_visible") and graspability == "graspable":
        errors.append("보이지 않는 약통은 집을 수 있음으로 저장할 수 없습니다.")
    if destination != "none" and destination_visibility == "not_visible":
        errors.append("목적지를 선택했는데 바구니가 보이지 않습니다. 바구니를 화면에 넣거나 목적지를 정하지 않음으로 선택하세요.")
    if state == "fallen" and strategy == "lid_grasp":
        errors.append("쓰러진 약통에는 뚜껑 집기를 선택할 수 없습니다.")
    if state == "upright" and skill == "pick_fallen":
        errors.append("세워진 상태에는 쓰러진 약통 집기를 선택할 수 없습니다.")
    if (state == "not_visible" or visibility == "not_visible") and skill == "pick_upright":
        errors.append("보이지 않는 약통에는 세워진 약통 집기를 선택할 수 없습니다.")
    if destination == "none" and skill in {"pick_upright", "pick_fallen"}:
        errors.append("목적지가 정해지지 않았으면 집기 작업을 선택할 수 없습니다.")
    if target == "none" and graspability == "graspable":
        errors.append("약통이 없으면 집을 수 있음으로 저장할 수 없습니다.")
    if target == "none" and state != "not_visible":
        errors.append("약통 없음이면 상태는 보이지 않음이어야 합니다.")
    if target == "multiple" and state != "mixed":
        errors.append("여러 종류 함께 있음이면 상태는 여러 상태가 섞임이어야 합니다.")
    return errors

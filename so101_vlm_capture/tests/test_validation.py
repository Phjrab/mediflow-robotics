import pytest

from so101_capture.validation import validate_labels


def valid_labels():
    return {
        "target_class": "medicine_a", "object_state": "upright", "visibility": "visible",
        "graspability": "graspable", "destination": "basket_1", "destination_visibility": "visible",
        "grasp_strategy": "lid_grasp", "capture_phase": "before_grasp", "action_result": "running",
        "required_skill": "pick_upright", "instruction": "medicine_a를 알맞은 바구니에 넣어",
        "needs_approval": True,
    }


@pytest.mark.parametrize("updates", [
    {"visibility": "not_visible", "graspability": "graspable"},
    {"object_state": "fallen", "grasp_strategy": "lid_grasp"},
    {"object_state": "upright", "required_skill": "pick_fallen"},
    {"object_state": "not_visible", "required_skill": "pick_upright"},
    {"destination": "none", "required_skill": "pick_upright"},
    {"target_class": "none", "graspability": "graspable", "object_state": "not_visible"},
])
def test_contradictions_are_rejected(updates):
    labels = valid_labels(); labels.update(updates)
    assert validate_labels(labels)


def test_valid_labels_are_accepted():
    assert validate_labels(valid_labels()) == []

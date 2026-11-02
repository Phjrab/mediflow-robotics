from __future__ import annotations

import threading

from .command import parse_sort_command
from .config import SystemConfig
from .errors import SorterError
from .models import TaskPlan, TaskResult
from .robot import RobotController
from .vision import load_scene, select_basket, select_target


STAGES = (
    "command_parsed", "scene_validated", "target_selected", "destination_verified",
    "path_checked", "pregrasp", "approach", "gripper_close", "short_lift",
    "grasp_check", "basket_preapproach", "basket_place", "gripper_open",
    "retreat", "placement_check", "standby_return",
)


class SorterApplication:
    def __init__(self, config: SystemConfig, controller: RobotController) -> None:
        self.config = config
        self.controller = controller
        self._lock = threading.Lock()

    def run_recorded_mock(self, command_text: str, scene_path) -> TaskResult:
        if not self._lock.acquire(blocking=False):
            raise SorterError("task_already_running", True, "동시에 한 작업",
                              "현재 작업이 끝난 뒤 다시 요청해 주세요")
        try:
            command = parse_sort_command(command_text)
            scene = load_scene(scene_path, self.config, recorded=True)
            target = select_target(scene, command)
            basket = select_basket(scene, command, self.config)
            plan = TaskPlan(target.observation_id, basket.marker_id, STAGES,
                            "recorded_mock", False)
            completed = self.controller.run(plan)
            return TaskResult("MOCK_COMPLETED", command, target.observation_id,
                              basket.marker_id, completed, "MOCK_ONLY", False)
        finally:
            self._lock.release()

    def run_live(self, command_text: str, scene_path) -> TaskResult:
        readiness = self.config.readiness("full_sort")
        if not readiness["ready"]:
            raise SorterError("live_configuration_incomplete", readiness,
                              "모든 새 환경 calibration verified",
                              "status 출력의 미검증 항목을 실측해 주세요")
        raise SorterError("live_motion_plan_unverified", command_text,
                          "독립 검증점과 감독 경로 시험을 통과한 joint plan",
                          "새 환경 단일 감독 시험 계획을 먼저 승인해 주세요")

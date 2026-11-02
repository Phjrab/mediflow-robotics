from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_config
from .errors import SorterError
from .fixed_demo import (compare_actual_motion_logs, compare_pose_motion, load_demo_settings,
                         DEFAULT_DEMO_MOTION_PROFILE,
                         DEFAULT_FREE_SPACE_VELOCITY,
                         DEFAULT_SMOOTH_CONTROL_HZ,
                         FREE_SPACE_VELOCITY_CHOICES,
                         MOTION_PROFILES,
                         SMOOTH_CONTROL_HZ_CHOICES,
                         parse_destination_rules, run_demo,
                         run_free_space_motion_test, save_roi_preview, teach)
from .robot import (LiveRobotAdapter, MockRobotAdapter, RobotController,
                    SO101ReadOnlyStatusAdapter)
from .workflow import SorterApplication


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m medicine_sorter")
    parser.add_argument("--config", type=Path, default=Path("configs/system.example.json"))
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("status", help="설정 준비 상태만 확인하며 장치를 열지 않습니다")
    sub.add_parser("validate-config", help="설정 schema를 검사하며 장치를 열지 않습니다")
    sub.add_parser("robot-status", help="follower를 쓰기 없이 1회 연결·상태 조회·해제합니다")
    teach_parser = sub.add_parser("teach", help="leader relative-start로 고정 데모 자세를 저장합니다")
    teach_parser.add_argument("--overwrite", action="store_true",
                              help="기존 pose JSON을 명시적으로 교체합니다")
    teach_parser.add_argument("--resume", action="store_true",
                              help="매 자세마다 저장된 partial JSON에서 이어갑니다")
    teach_parser.add_argument("--only", nargs="+", metavar="POSE",
                              help="기존 pose 파일에서 지정한 자세만 추가하거나 다시 저장합니다")
    preview_parser = sub.add_parser(
        "roi-preview", help="로봇 연결 없이 Astra 한 프레임에 고정 칸 ROI를 그려 저장합니다")
    preview_parser.add_argument("--output", type=Path, required=True,
                                help="저장할 PNG 경로")
    run_parser = sub.add_parser("run", help="ArUco 고정 칸→티칭 자세 재생 데모")
    run_mode = run_parser.add_mutually_exclusive_group()
    run_mode.add_argument("--dry-run", action="store_true",
                          help="카메라 1프레임으로 계획만 출력하고 로봇을 열지 않습니다")
    run_mode.add_argument("--step", action="store_true",
                          help="각 단계 Enter 확인 후 실행합니다")
    run_mode.add_argument("--auto", action="store_true",
                          help="검증된 배치에서 Enter 없이 모든 단계를 연속 실행합니다")
    run_parser.add_argument("--rule", nargs="+", metavar="TYPE=COLOR",
                            help="예: --rule C=green B=red A=blue")
    run_parser.add_argument(
        "--profile", choices=MOTION_PROFILES, default=DEFAULT_DEMO_MOTION_PROFILE,
        help="동작 profile (기본 smooth 40Hz·20°/s, 기존 방식은 original)",
    )
    run_parser.add_argument(
        "--warm-start-once", action="store_true",
        help="감독 --step 1회에서만 시작 온도 50–51°C를 허용합니다(실행 중 55°C 제한 유지)",
    )
    compare_parser = sub.add_parser(
        "motion-compare", help="장치를 열지 않고 동일 pose 구간의 original/smooth 명령을 비교합니다")
    compare_parser.add_argument("--from-pose", required=True)
    compare_parser.add_argument("--to-pose", required=True)
    compare_parser.add_argument("--actual-log", type=Path,
                                help="motion-test JSONL이 있으면 실제 cadence/feedback도 분석합니다")
    analyze_parser = sub.add_parser(
        "motion-analyze", help="장치를 열지 않고 ORIGINAL/SMOOTH JSONL을 비교합니다")
    analyze_parser.add_argument("--original-log", type=Path, required=True)
    analyze_parser.add_argument("--smooth-log", type=Path, required=True)
    motion_test_parser = sub.add_parser(
        "motion-test", help="동일한 두 티칭 자세 사이 한 free-space 구간만 감독 실행합니다")
    motion_start = motion_test_parser.add_mutually_exclusive_group(required=True)
    motion_start.add_argument("--from-pose")
    motion_start.add_argument(
        "--from-current", action="store_true",
        help="fresh HOLD 뒤 현재 자세를 시작점으로 고정합니다")
    motion_test_parser.add_argument("--to-pose", required=True)
    motion_test_parser.add_argument("--profile", choices=MOTION_PROFILES, required=True)
    motion_test_parser.add_argument(
        "--control-hz", type=int, choices=SMOOTH_CONTROL_HZ_CHOICES,
        help=(f"smooth 전용 absolute-deadline 주기(기본 {DEFAULT_SMOOTH_CONTROL_HZ}Hz)"),
    )
    motion_test_parser.add_argument(
        "--free-space-velocity", type=int, choices=FREE_SPACE_VELOCITY_CHOICES,
        help=("smooth free-space arm 속도 상한; 첫 A/B는 "
              f"기본 {DEFAULT_FREE_SPACE_VELOCITY}°/s 유지"),
    )
    motion_test_parser.add_argument("--log", type=Path, required=True,
                                    help="덮어쓰지 않을 새 .jsonl 경로")
    motion_test_parser.add_argument("--step", action="store_true",
                                    help="현장 확인 Enter 후 한 구간만 실행합니다")
    motion_test_parser.add_argument(
        "--stable-hold-gap-raw", type=int, default=10,
        help="감독 motion-test의 already-ON 정지 Goal−Present 허용치(10..60 raw, 기본 10)",
    )
    web_parser = sub.add_parser("web", help="고정 데모 버튼 UI를 실행합니다")
    web_parser.add_argument("--port", type=int, default=8030,
                            help="같은 네트워크에 공개할 HTTP port (기본 8030)")
    web_parser.add_argument(
        "--profile", choices=MOTION_PROFILES, default=DEFAULT_DEMO_MOTION_PROFILE,
        help="웹 실행 profile (기본 smooth 40Hz·20°/s, 기존 방식은 original)",
    )
    mock = sub.add_parser("mock-run", help="저장 scene으로 전체 상태 흐름을 시험합니다")
    mock.add_argument("--scene", type=Path, required=True)
    mock.add_argument("--command", required=True)
    live = sub.add_parser("live", help="새 환경 검증 전에는 장치 연결 전에 차단됩니다")
    live.add_argument("--scene", type=Path, required=True)
    live.add_argument("--command", required=True)
    return parser


def main(argv=None, *, status_adapter_factory=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if args.mode in {"status", "validate-config"}:
            payload = {"status": "CONFIG_VALID",
                       "task_readiness": config.readiness_by_task(),
                       "serial_open": 0, "camera_open": 0, "motor_write": 0}
        elif args.mode == "robot-status":
            readiness = config.readiness("robot_status")
            if not readiness["ready"]:
                raise SorterError("robot_status_configuration_incomplete", readiness,
                                  "follower port·USB serial 설정",
                                  "Jetson에서 follower 식별 정보를 확인해 주세요")
            factory = status_adapter_factory or SO101ReadOnlyStatusAdapter.from_config
            payload = RobotController(factory(config)).read_status().as_dict()
        elif args.mode == "teach":
            payload = teach(config, load_demo_settings(config), overwrite=args.overwrite,
                            resume=args.resume, only=args.only)
        elif args.mode == "roi-preview":
            payload = save_roi_preview(load_demo_settings(config), args.output)
        elif args.mode == "run":
            settings = load_demo_settings(config)
            rules = parse_destination_rules(args.rule, settings.destinations)
            payload = run_demo(config, settings, dry_run=args.dry_run,
                               step=args.step, auto=args.auto,
                               warm_start_once=args.warm_start_once,
                               motion_profile=args.profile, rules=rules)
        elif args.mode == "motion-compare":
            payload = compare_pose_motion(
                config, load_demo_settings(config),
                from_pose=args.from_pose, to_pose=args.to_pose,
                actual_log=args.actual_log)
        elif args.mode == "motion-analyze":
            payload = compare_actual_motion_logs(args.original_log, args.smooth_log)
        elif args.mode == "motion-test":
            payload = run_free_space_motion_test(
                config, load_demo_settings(config),
                from_pose=args.from_pose, to_pose=args.to_pose,
                from_current=args.from_current, motion_profile=args.profile,
                log_path=args.log, step=args.step,
                stable_hold_gap_raw=args.stable_hold_gap_raw,
                control_hz=args.control_hz,
                free_space_velocity=args.free_space_velocity)
        elif args.mode == "web":
            if not 1 <= args.port <= 65535:
                raise SorterError("web_port_invalid", args.port, "1..65535",
                                  "사용 가능한 port를 지정해 주세요")
            from .web import serve_demo_web
            serve_demo_web(config, load_demo_settings(config), port=args.port,
                           motion_profile=args.profile)
            payload = {"status": "WEB_STOPPED", "host": "0.0.0.0", "port": args.port,
                       "motion_profile": args.profile}
        elif args.mode == "mock-run":
            app = SorterApplication(config, RobotController(MockRobotAdapter()))
            payload = app.run_recorded_mock(args.command, args.scene).as_dict()
        else:
            app = SorterApplication(config, RobotController(LiveRobotAdapter(config.readiness())))
            payload = app.run_live(args.command, args.scene).as_dict()
    except SorterError as error:
        print(json.dumps(error.as_dict(), ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0

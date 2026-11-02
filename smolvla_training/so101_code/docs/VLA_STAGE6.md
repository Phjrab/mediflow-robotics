# VLA 6단계 — 평가 원장과 오프라인 집계

2026-10-29 KST. 실제 평가 기록은 **0건**이다. `data/vla_eval.csv`는 헤더만 제공한다.
새 도구는 Python 표준 라이브러리만 사용하며 로봇·카메라·모델·GPU·네트워크를 호출하지 않는다.
5단계 커밋 `27f9c797ff8071f61bb6e81439cb4722d61c4d95`를 보존했다.

## 기록 스키마

한 CSV는 체크포인트별 **한 평가 라운드**다. 각 체크포인트에 9조합 × 5회(45회)를
기록하며 새 라운드는 새 CSV를 사용한다. 날짜가 달라져도 기존 trial을 새 시험으로 세지 않는다.
조합: A/B/C × red/blue/green. A=큰 약통, B=중간, C=작은 약통.

| 열 | 의미/형식 |
|---|---|
| date | YYYY-MM-DD(KST) 또는 시간대가 있는 ISO 시각. 수정행은 같은 시험의 이전 날짜 이상 |
| checkpointhash | 평가한 모델 파일의 소문자 SHA256 64자리. 파일 경로·모델 별명은 불가 |
| combo | A:red 등 9개 조합 |
| trial | 1~5. 체크포인트·조합 내 고유 시험 ID |
| loaded | 정책 로드 성공 0/1. 약통을 적재했다는 뜻 아님 |
| moved | 해당 시험에서 정책으로 움직였는지 0/1 |
| grasped | 실제 파지가 이루어졌는지 0/1. 잘못된 약통 파지도 기록하되 실패 분류 필수 |
| correct_bin_placed | 올바른 약통을 지정 바구니에 놓고 그리퍼에서 분리한 완료 상태 0/1 |
| failure_type | wrong_bottle / wrong_bin / grasp_failure / drop / safety_stop / other. 성공행만 빈칸 |
| notes | 관찰 또는 수정 이유. 수정행은 필수. 개인정보 대신 짧은 실험 메모 |
| reviewer | 빈칸 불가. 기록자 별칭 권장; 개인정보를 저장소에 커밋하지 않음 |

각 실패값의 뜻: 잘못된 약통 / 잘못된 바구니 / 파지 실패 / 낙하 / 안전 정지 / 기타.
중단 원인이나 로드 실패는 실패로 기록하며 아직 하지 않은 시험은 **행을 만들지 않는다**.
`loaded → moved → grasped → correct_bin_placed`의 앞 단계가 0인데 뒤 단계가 1이면 거부한다.
wrong_bin/drop은 grasped=1, grasp_failure는 moved=1/grasped=0, wrong_bottle은 moved=1이어야 한다.
낙하했다 다시 성공한 경우에는 최종 성공과 중간 사건을 메모로 남기거나 별도 trial로 재시험한다.
다중 실패 원인이 있으면 대표 원인 하나와 메모를 기록한다. 이 스키마는 들어올리기 단계의 별도 열은 없다.

숫자 bool·JSON bool이 아니라 CSV에 맞춘 문자열 `"0"`/`"1"`을 받는다.
제어문자·과도한 길이·수식처럼 시작하는 메모/기록자를 거부한다. 원장에는 수식을 넣지 않는다.
SHA는 형식만 검사하며 실제 checkpoint와의 일치·관찰의 진실성을 자동 인증하지 않는다.
동일 가중치에서 전처리·제어 설정이 달라졌다면 새 라운드 원장과 별도 run manifest로 구분해야 한다.

## 수정 이력·저장 보장

- 키는 `(checkpointhash, combo, trial)`. 새 시험은 중복을 거부한다.
- 수정은 `--correction` + 이유 메모로 **새 행을 추가**한다. 이전 행을 지우거나 바꾸지 않는다.
- 집계는 같은 키의 마지막 물리적 행 하나만 사용한다. 시각 정렬로 임의 승자를 고르지 않는다.
- 잘못된 행/CSV/누락된 마지막 줄바꿈/과거 날짜의 수정은 저장 전에 거부한다.
- 별도 `.lock` 파일의 POSIX flock으로 협력 writer를 직렬화하고 예상 SHA로 오래된 입력을 거부한다.
- 기존 원본 bytes + 새 CSV 행을 동일 디렉터리 임시 파일에 쓰고 fsync → os.replace → 디렉터리 fsync한다.
  성공 시 기존 bytes가 결과의 정확한 prefix이며, 쓰기/교체 전 실패 시 원본이 유지된다.
- 교체 후 디렉터리 fsync 실패는 **이미 저장되었을 수 있음**을 오류로 알린다. SHA를 다시 읽기 전 재시도하지 않는다.
- symlink 원장/lock을 거부한다. lock 파일은 삭제하지 않는다. 원장 inode는 교체되므로 외부 프로그램은 재열기해야 한다.
- 이 보장은 로컬 POSIX 파일시스템에서 이 API를 사용하는 writer에 대한 것이다. 네트워크 FS, 수동 동시편집,
  악의적인 파일 교체, 전원손실 전반에 대한 불변 저장소/암호학적 감사 보장은 아니다.
- 보고서는 매번 새 디렉터리만 허용하고 원장·기존 보고서를 덮어쓰지 않는다. 보고서 도중 디스크 오류가 나면
  해당 새 디렉터리는 불완전할 수 있으므로 다른 새 경로로 재생성한다.

## 집계 규칙

- 체크포인트별 9행 × trial 1~5 표시. 미기록 trial은 JSON null / 표시 N/A.
- 정책 로드·움직임·파지·지정 바구니 투입을 따로 집계한다.
- 성공률 분자 = correct_bin_placed=1. 분모 = 그 체크포인트의 **기록된 고유 완료 시험 수**.
  로드 실패도 분모에 포함하며 미기록은 포함하지 않는다. 완료한 시험에는 실패/중단 시험도 포함된다.
- 기록 0건이면 성공률은 null/N/A. 실패 기록 1건이면 0/1=0%로 명확히 구분한다.
- 단계별 비율도 같은 분모이며 조건부 파지율 등이 아니다. 기록/계획·미기록 수를 함께 표시한다.
- 체크포인트 사이의 성공률을 합산하지 않는다. 같은 날짜 또는 모델 별명으로 합치지 않는다.
- 5회는 초기 비교용 기록 양식이지 통계적으로 충분한 성능 인증이 아니다. 부분 기록의 높은 비율을 최종 성능으로 해석하지 않는다.
- 합성 시험은 `SYNTHETIC_NOT_ROBOT_RESULTS`로 표시한다. 일반 원장도 사람이 입력한 미독립검증 자료다.
  코드 PASS와 실로봇 성공률을 구별한다.

## 명령

저장소 루트에서 실행한다. 원본 템플릿은 그대로 두고 실제 기록은 `.local/`에 새 복사본을 만든다.
아래 실제 평가 행 추가는 **이번에 수행하지 않았다**. `row.json`은 위 11개 키만 담은 문자열 객체다.

```bash
# 검증: 읽기 전용, 예상 SHA 확인
python3 -B scripts/vla_eval_report.py --csv data/vla_eval.csv validate

# 실제 기록을 넣지 않은 템플릿으로도 N/A 보고서 생성 가능; 매번 새 경로
python3 -B scripts/vla_eval_report.py --csv data/vla_eval.csv report --out .local/vla_eval_report_NEW

# 별도로 준비한 로컬 원장에 시험 결과 한 건 추가 (이번 NOT_RUN)
python3 -B scripts/vla_eval_report.py --csv .local/vla_eval_round1.csv append --row-json .local/row.json --expected-sha256 <validate에서_확인한_SHA>

# 같은 시험을 정정: 새 시각/수정 이유를 가진 row.json, 이전 행 보존 (이번 NOT_RUN)
python3 -B scripts/vla_eval_report.py --csv .local/vla_eval_round1.csv append --row-json .local/correction.json --expected-sha256 <현재_SHA> --correction
```

CLI 성공 exit0, 입력/저장 오류 exit2. 실험 행은 비공개 로컬에 보관하고 커밋하지 않는다.
보고서에는 원본 SHA와 이력행 수·고유시험 수·수정 전 행 수를 기록하며 메모와 기록자는 노출하지 않는다.

## 검증과 남은 한계

신규/기존 시험 결과 및 실행 명령은 `docs/sessions/2026-10-29-vla-stage6-evaluation.md`와
`reports/vla-stage6-20261029/verification.json` 참조. 합성 값은 tests의 임시 디렉터리에서만 생성했다.
스프레드시트 스킬의 원본보존·중복키·분모·결측치 구분 기준을 적용했다. 요청된 CSV/코드 범위를 유지해
별도 XLSX·차트는 만들지 않았고, Excel 수식 또는 Excel 재계산을 검증했다고 주장하지 않는다.

5단계 실연결 한계는 그대로다:

- `medicine_sorter/vla_policy_adapter.py:70` UnitContract는 명시 단위 계약의 **모의 검증**이다.
  :82에서 단위를 검사하고 :92/:97에서 arm radian↔degree를 변환한다. gripper는 percent 별도 계약이다.
  실제 데이터·정책·로봇 단위/보정 대응은 미검증이다.
- :139 start_gate는 synthetic mean/tolerance·monotonic timestamp를 사용하는 fixture 계약이다.
  실제 감사자료의 시작 분포와 UTC timestamp 연결은 미완료다.
- :155 MockStreamingController는 실제 RobotController가 아니다. :166 readiness가 실shadow/live를 차단한다.
  연속 목표 전달/HOLD·실관측 provider·fresh 소유권 증거가 없어 CLI 승인값으로 실행 가능하게 만들지 않았다.
- 기존 RobotController/fixed_demo/calibration/안전 제한/데이터/모델은 수정하지 않았다.
  이번 범위에서 장치 open·Jetson 접속·GPU probe·실추론·다운로드·학습·서비스 변경·push는 모두 0이다.
  7단계 웹은 시작하지 않았다. `NOT_RUN / BLOCKED / NOT_PUSHED`.

복귀 첫 명령: `bash scripts/jetson_preflight.sh --dry-run` (장치 연결 성공을 뜻하지 않음).
실제 장치 확인/녹화/학습/동작은 각각 필요한 환경·현장 승인과 별도의 연결 검증 후 진행한다.
rollback은 이번 6단계 commit을 revert한다. 5단계도 되돌리려면 그 다음 27f9c79를 revert한다.
원장에 실제 기록을 넣었다면 먼저 별도 복사해 보존하고 clean 상태에서 수행한다. reset --hard는 사용하지 않는다.

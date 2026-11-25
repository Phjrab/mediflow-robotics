# Jetson 데이터셋 및 10월 기록 목록

Jetson의 읽을 수 있는 홈 저장소와 캐시에서 LeRobot `meta/info.json` 64개를 찾았고 모두 파싱했다. 임시 비교본에는 분할 파일과 parquet만 있었다. 목록은 경로마다 버전, 회차·프레임 수, FPS, 카메라 형식, MP4 개수, 태스크 레이블, 이름의 날짜 표식과 메타데이터 파일 수정시각을 구분한다. 합계 595회·320,874프레임에는 중복 사본과 카메라 없는 진단 데이터가 포함된다. 36개 경로에서 MP4를 확인하지 못했고 28개 경로에서 별도 태스크 테이블을 읽었다. [메타데이터 목록](jetson_dataset_inventory.json)

목록의 이름에 2026-10-09 또는 2026-10-17이 들어간 데이터셋과 ArUco라는 이름의 관측 필드는 없었다. 날짜 표식과 파일 수정시각만으로 촬영일이나 영상에 나타난 마커를 결정할 수 없다.

Jetson에 보존된 별도 `so101-lelab-medicine-sorter` 아카이브에서 다음 문서와 설정을 확인했다.

- 10월 17일 `auto-web-demo`, `fixed-demo-roi-preview`, `gripper-stage-and-auto-port`, `runtime-rules-resumable-teach`, `type-specific-gripper-overload-relief` 세션 문서가 있다. ROI 문서는 3개 슬롯과 ArUco 중심이 표시된 미리보기를 설명한다. 보존 아카이브 및 홈 파일명 검색에서 해당 `fixed-demo-roi.png`를 찾지 못했다.
- 10월 9일 `c-red-complete-teleop-demo` 세션 문서가 있다. 문서가 설명한 시연의 원시 로그나 영상을 이 보존본에서 확보하지 못했다.
- `configs/fixed_demo_poses.json`은 12,767바이트이며 수정시각은 2026-10-26 KST다. `configs/fixed_demo_poses.partial.json`, `artifacts/`, `logs/raw/`, `reports/raw/`는 이 아카이브에 없었다.

# A → blue 본 녹화 첫 회 검수

데이터셋: Supermassive111/a_blue_main_20261102_192441
Hub revision: 7bbdea944abd1792549383c3d31cc555d637d618 (공개)

판정: 영상 저장 검수 PASS. Astra 색 불일치는 사용자 결정에 따라 허용. 실제 시연 성공/정책 성능은 판정하지 않음.

| 항목 | 실제 저장 파일 확인 |
|---|---|
| 형식 | LeRobot v3.0 |
| 에피소드 | 0 (총 1개) |
| 프레임 | 653 (약 21.77초) |
| 카메라 | observation.images.ceiling_vertical / ceiling_oblique / end_effector |
| 영상 | 각 653프레임 디코딩, 640×480, 30fps |
| state / action | 각각 모든 행에서 6차원 |
| task | Put medicine bottle A into the blue basket |
| 수치 | NaN/Inf 0, timestamp 간격 이상 0 (1ms 허용) |
| 중간 이미지 | 10.9초의 세 이미지 모두 비어 있거나 깨지지 않음 |

동일 폴더의 episode_000_ceiling_vertical_middle.png, episode_000_ceiling_oblique_middle.png, episode_000_end_effector_middle.png와 inspection.json 참조.

원본 데이터/체크포인트 변경 없음. 저장 파일을 내려받아 읽기만 했으며 카메라·serial 열기, 녹화, 브리지 변경, 로봇 구동, 학습, 재부팅, push는 하지 않았다.
촬영 조건과 task를 유지해 사용자가 30~50회를 이어서 수집한다. 실패한 회차는 지우지 않고 번호/사유를 기록한다.

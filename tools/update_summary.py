"""보존된 검증 결과에서 경로표와 시나리오별 보고서를 만든다."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REPORTS=ROOT/'reports/20261112'

def read(name):return json.loads((REPORTS/name).read_text(encoding='utf-8-sig'))

def main():
    moves=['# 이동 전 → 이동 후','', '삭제하지 않고 이동했다. 기준 경로는 Windows 자율설계/, Jetson ~/다. 원본 보호 예외는 PATHS.md를 따른다.','']
    for platform in ['windows','jetson']:
        obj=read('MOVES_'+platform+'_APPLIED.json');rows=obj if isinstance(obj,list) else obj['moves']
        moves += ['## '+platform,'','| 이동 전 | 이동 후 | 결과 |','|---|---|---|']
        for row in rows:
            a=row['from'].replace('\\','/');b=row['to'].replace('\\','/')
            moves.append(f'| `{a}` | `{b}` | {row.get("status","MOVED")} |')
        moves.append('')
    moves += ['## 추가 정리','',
              '- Windows 루트의 옛 시작 안내·백업·재시작 프롬프트 → `_archive/20261110/windows/root_notes/`.',
              '- 중복 모듈4개 → `_archive/20261110/duplicate_modules/`; 미사용 의존 모듈11개 → `_archive/20261110/unused_deps/`.',
              '- 배포 중 이전 소스는 `_archive/20261110/pre_*`에 보존하고 Git 커밋으로 버전을 남겼다.',
              '- Windows `reports/overnight_20261104` 잔여물은 권한 제한으로 남겼다. 전체 복사본은 날짜별 reports에 있으며 Git fsck 검증과 bundle 보존을 완료했다.',
              '- 원본 보정·모델·데이터가 든 상위 폴더는 통째로 이동하지 않았다. 삭제 후보는 DELETE_CANDIDATES.md에만 적었다.']
    (ROOT/'MOVES.md').write_text('\n'.join(moves)+'\n',encoding='utf-8')
    sim=read('SIMULATION_RESULTS.json');audit=read('MODEL_AUDIT.json');camera=read('CAMERA_CHECK.json');dry=read('CAMERA_MODEL_DRY.json')
    names={'all_off':'6축 토크OFF','partial_off':'일부 축 토크OFF','outside_home':'홈 밖15°','cooling':'52→47→44°C 냉각','busy_port':'포트 점유·해제','intermittent_camera':'카메라 간헐 실패','slow_inference':'재생보다 느린 추론','blocked_gripper':'그리퍼 막힘·조임 완화','ctrl_c':'Ctrl+C','overheat':'55°C','usb_disconnect':'USB 단절','elbow_plus_1':'팔꿈치 상한+1틱','elbow_plus_5':'팔꿈치 상한+5틱','elbow_plus_20':'팔꿈치 상한+20틱','wrist_plus10':'손목 홈+10°','wrist_minus10':'손목 홈−10°'}
    out=['# 정리·실행기 통합 결과 — 2026-11-12','',
         '**소프트웨어·모의 검증을 완료했지만 실제 카메라 dry-run은 검은 영상으로 차단됐다. 전체 실기 준비 완료나 로봇 성공으로 보고하지 않는다.**','',
         '## 변경','',
         '- Windows78개·Jetson189개 항목 이동. Windows1곳은 전체 복사 후 원본 권한 잔여물을 보존했다. 모델·데이터·보정은 원위치다.',
         '- 최신 실행기1벌과 공통config, 자동 토크ON·범위 복구·홈minimum-jerk·냉각 대기·서비스 인수/복원·tmux를 구성했다.',
         '- 학습에서 확인한 증가=그리퍼 열림을 따라 조임 완화를 현재값−4점으로 적용한다. 열기 목표는 즉시 해제한다.',
         '- 팔꿈치 명령상한은3082로 통일했다. 어깨 들기는 보정하한796+5틱을 적용했지만 실제 스토퍼 실측이라고 주장하지 않는다.',
         '- 0.33배 모델 타임라인·60Hz보간·추론 선행 요청·경과 액션 건너뛰기·10액션 블렌딩을 유지하고, 추론이 늦으면 재생률을 더 낮춘다.',
         '- USB·응답 오류에서는 추가 쓰기를 차단한다. 통신이 끊기면 현재 위치 유지 전송은 보장할 수 없어 HOLD_NOT_CONFIRMED를 기록한다.',
         '- 모드 오류·보정/모델 계약 불일치·잘못된 패킷을 경고만 내고 실행하지 않는다. 금지된 모드/보정 쓰기로 자동 수정할 수 없는 조건이다.',
         '- LeLab 설치 Python은 변경하지 않았다. 홈 HTML·관제 서비스·새 읽기 전용 설정 서비스와 Astra 별칭만 배포했다.','',
         '## 검증','',
         '실제 모델과 녹화3장, 가짜1차지연 서보 버스가 동일 prepare/stream 함수를 사용했다. 아래16조건은 각20초 가상시간이다. 모델 계산은 실제 Jetson에서 수행했다. 시작 단계 쓰기는 가짜 버스뿐이다.','',
         '| 조건 | 결과 | 목표 수 | 시작 목표 오차 최대(틱) |','|---|---|---:|---:|']
    for row in sim['cases']:
        out.append(f'| {names[row["case"]]} | {"PASS" if row["passed"] else "FAIL"} / {row["status"]} | {row["steps"]} | {row["max_arm_error_ticks"]} |')
    out += ['',f'180초 가상시간 추가 검증: {", ".join(names[x["case"]] for x in sim["full_180_cases"])}. 모두 PASS. 전체OFF·일부OFF의 시작 목표 오차는3틱 이내였다.',
            '단위 검사5개 Windows/Jetson PASS, bash 문법 PASS, 최종 실제 모델 로딩·예열 PASS. 16조건 결과는 JSON으로 보존했다. 첫 가짜 버스의 근사 그리퍼 환산 실패와 정리 후 옛 프리뷰 경로 실패는 로그를 보존하고 수정했다.','',
            '## 모델·인식','',
            'checkpoint와 학습 policy의use_amp/디노이징10/VLM16/50·50/정규화/패딩512×512가 모두 같았다. 별도 .half() 변환은 없고 저장 AMP를 사용한다. 원본 RGB640×480을 /255로 만들고 저장된 rename과 정규화는 한 번만 적용한다.',
            '기존1,057프레임 검증은 실제 quality.json으로 확인했다. RTC는 오차가 커 제외했으며 이번에도 False다. 새로3회차×시작·중간·후반9관측, 예측450액션을 실제 녹화와 비교했다.','',
            '| 관절 | 새450액션 MAE | 첫 액션9개 MAE |','|---|---:|---:|']
    joints=['어깨 회전','어깨 들기','팔꿈치','손목 굽힘','손목 회전','그리퍼(점)']
    for i,j in enumerate(joints):
        a=sum(x['chunk_mae'][i] for x in audit['episodes'])/3;b=sum(x['first_action_mae'][i] for x in audit['episodes'])/3
        out.append(f'| {j} | {a:.3f} | {b:.3f} |')
    out += ['', '새 일반 추론은 약1.41~1.46초였다. 이 오차는 약통 집기 성공률이 아니다. 기존1,057프레임의 첫액션 전수오차와 새50액션 오차는 표본·지평이 달라 직접 개선량으로 비교하지 않는다.','',
            '## 실제 카메라','',
            '| 카메라 | 현재 밝기 평균 | 학습 밝기 평균 |','|---|---:|---:|']
    for role in ['end_effector','ceiling_vertical','ceiling_oblique']:
        out.append(f'| {role} | {camera[role]["current"]["mean_rgb"]:.2f} | {camera[role]["training"]["mean_rgb"]:.2f} |')
    out += ['', '손목USB와RealSense는 자동 노출·자동WB가 이미 켜져 있었다. Astra loopback에는 노출 컨트롤이 없다. 현재3장 모두 거의 검으며, 조명 또는 렌즈 가림의 현장 확인이 필요하다. 학습 당시의 정확한 수동 노출값은 기록이 없어 미확인이다. 밝기만 억지로 키워 정상 영상이라고 처리하지 않았다.',
            '세 카메라의 RGB640×480 프레임과USB정체성은 확인했다. 현재 영상이 검어 구도·회전·색상 일치를 시각적으로 확정하지 못했다. Astra는 학습 때의 녹색 편향이 담긴 같은 브리지를 유지했다. RealSense 첫 읽기 실패3회가 남았고 프레임 공백 최대는 약0.07초였다. 예열 프레임은 정책 입력 전에 버린다.',
            f'실제 카메라+실제 모델+가짜 모터 dry-run: **{dry["status"]}, {dry["steps"]}스텝**, exit1. 원인: 검은 영상이 usable frame 검사에서 제외됨. 실제 전송Hz·묶음 대기시간은 목표가 없어 측정 불가이며0으로 성공 표시하지 않는다.',
            '[학습/현재 비교 이미지](camera_comparison.png). 모델 모의 입력의 토크·온도는 실물6축 상태가 아니다.','',
            '## 보존·남은 문제','',
            '- 두 PC의 모델b84b15c7…와보정2f74c130… 해시 일치. 원본 데이터 이동·삭제·재학습 없음.',
            '- 실제 모터 목표·토크·그리퍼 한계·보정·PID·가속도 쓰기0회. live NOT_RUN. 기존 LeLab venv/torch 교체 없음.',
            '- 현장 원래6축 배선 복원·조명/가림 확인이 남았다. 현재 실기 분리 구성은 ID5·6만 응답한 상태였다.',
            '- 온도 급변 원인 미확인. 55°C 및 상태/통신 오류 보호 유지. 잘못된 상태를 시작 통과로 위장하지 않는다.',
            '- GitHub push는 인증 부재로 거절됐다.911f3ee/1c59fdb 포함3개 Windows저장소 bundle과Jetson legacy bundle을 보존했다. 실제 원격 보존 완료라고 쓰지 않는다.',
            '- Windows 옛 Git 잔여물1곳은 권한 제한으로 남았다. 전체 복사본 fsck와bundle 검증 기록을 보존한다.',
            '- LeLab·홈·관제는 노트북에서HTTP200. 텔레옵은 켜지 않아6축 실시간 방송은 NOT_RUN.',
            '- 이는 소프트웨어 검증이며 G5/G6 또는 물리 성공이 아니다.','',
            '## 실행','', '조명·원래6축 배선을 확인한 뒤 사용할 명령이며, 이번 작업에서는 실행하지 않았다.','',
            '```bash','bash ~/so101_vla/live/run.sh --live','```']
    (REPORTS/'REPORT.md').write_text('\n'.join(out)+'\n',encoding='utf-8')
    print('MOVES_AND_REPORT_WRITTEN')
if __name__=='__main__':main()

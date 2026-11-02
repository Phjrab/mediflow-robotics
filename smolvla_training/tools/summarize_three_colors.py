import json
from pathlib import Path
import sys

out = Path(sys.argv[1])
data = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
datasets = data['datasets']
exclusions = data['exclusions']['datasets']
lines = ['# A 약통 3색 데이터 점검 결과', '',
         '저장된 Hub 파일만 점검했다. 로봇 성공 가능성/정책 성능/시연 성공률은 판정하지 않는다.', '',
         '번호는 사용자의 “번째 에피소드”라는 답변에 따라 1부터 센 순서로 해석했고, LeRobot episode_index = 순서 - 1로 환산했다.', '',
         '## 데이터셋별 결과', '',
         '| 색 | 데이터셋 | 에피소드 | 프레임 | 제외 후 에피소드/프레임 | 형식 | fps | state/action |',
         '|---|---|---:|---:|---|---|---:|---|']
for color, d in datasets.items():
    x = exclusions[color]
    lines.append(f"| {color} | {d['repo_id']} | {d['episodes']} | {d['frames']:,} | {len(x['included_episodes'])} / {x['remaining_frames']:,} | {d['format']} | {d['fps']} | {d['state_dims']} / {d['action_dims']} |")
lines += ['', '카메라 키는 observation.images.ceiling_vertical(Astra), observation.images.ceiling_oblique(RealSense), observation.images.end_effector(손목)다. 실제 파일별 해상도·fps와 프레임 수는 아래 영상 점검 표와 audit JSON에 기록한다. Astra의 알려진 색 차이로 제외하지 않았다.', '',
          '관절 names/순서는 각 데이터셋 audit JSON의 features에 기록했다. 관절 단위를 값 범위로 추정하지 않았다.', '',
          '| 색 | task (모든 데이터 행/에피소드 메타 대조) | 정확히 일치 | Hub revision |',
          '|---|---|---|---|']
for color,d in datasets.items():
    lines.append(f"| {color} | {d['expected_task']} | {d['all_tasks_exact']} | {d['revision']} |")
lines += ['', '## 사용자 지정 제외 — 실제 데이터는 그대로 보존', '',
          '| 색 | 사용자 순서(1부터) | LeRobot episode_index(0부터) | 프레임 | 초 | 사유 |',
          '|---|---:|---:|---:|---:|---|']
for color,x in exclusions.items():
    for e in x['excluded_details']:
        lines.append(f"| {color} | {e['user_episode_number']} | {e['episode_index']} | {e['frames']} | {e['seconds']:.2f} | 사용자 실패 메모 |")
lines += ['', '길이는 실패 여부를 증명하지 않는다. 특히 red 2번째는 해당 데이터셋에서 가장 긴 회차다. 위 번호/길이를 사용자가 실패 회차와 대조할 수 있게 남겼으며, 길이로 메모를 재판정하지 않았다.', '',
          '## 떨굼 참고 메모 — 제외하지 않음', '',
          '| 색 | 사용자 순서 | LeRobot index | 프레임 | 초 | 처리 |',
          '|---|---:|---:|---:|---:|---|']
for color,x in exclusions.items():
    for e in x['reference_notes']:
        lines.append(f"| {color} | {e['user_episode_number']} | {e['episode_index']} | {e['frames']} | {e['seconds']:.2f} | 포함 유지 |")
lines += ['', '세워 넣음/떨굼을 학습 제외 조건으로 추가하지 않았다. 자동 점검 후보도 자동 제외하지 않는다.', '',
          '## 형식·수치·영상 점검', '',
          '짧음 후보: 2초 미만 또는 해당 데이터셋 중앙 길이의 50% 미만. 이 기준은 데이터 수집 오류를 단정하는 기준이 아니다.',
          '반복 후보: 디코딩한 RGB 픽셀이 연속으로 완전히 같은 run이 1초 이상. 정지 장면일 수 있으므로 카메라 멈춤/시연 실패로 단정하지 않는다.',
          '단색/검정 후보: 모든 RGB 채널의 프레임 내 범위가 2 이하 / 최대 픽셀값 3 이하. 압축 영상의 모든 시각적 결함을 검출하는 방법은 아니다.', '',
          '| 색 | 카메라 | 실제 해상도/fps | 디코딩 프레임 | 단색/검정 | 동일 연속쌍 | 최대 동일 run(초) | 파일 이슈 |',
          '|---|---|---|---:|---|---:|---:|---|']
compact = {}
for color,d in datasets.items():
    repeats=[]; camera_totals=[]
    for c in d['cameras']:
        key=c['key']; videos=[e['video'][key] for e in d['episode_checks']]
        frame_count=sum(v['frames'] for v in videos)
        uniform=sum(v['uniform_frames'] for v in videos)
        black=sum(v['black_frames'] for v in videos)
        pairs=sum(v['adjacent_identical_pairs'] for v in videos)
        longest=max((v['max_identical_seconds'] for v in videos), default=0)
        file_formats=sorted(set(str(f.get('resolution')) + '/' + str(f.get('fps')) for f in c['files']))
        camera_totals.append({'camera':key,'frames':frame_count,'uniform':uniform,'black':black,'pairs':pairs,'max_repeat_seconds':longest,'formats':file_formats,'issues':c['issues']})
        lines.append(f"| {color} | {key.split('.')[-1]} | {', '.join(file_formats)} | {frame_count} | {uniform}/{black} | {pairs} | {longest:.2f} | {', '.join(c['issues']) or '없음'} |")
        for ep in d['episode_checks']:
            v=ep['video'][key]
            if v['long_repeat_candidate']:
                repeats.append({'episode_index':ep['episode_index'],'user_episode_number':ep['user_episode_number'],'camera':key,'run_frames':v['max_identical_run'],'run_seconds':v['max_identical_seconds']})
    short=[{'episode_index':e['episode_index'],'user_episode_number':e['user_episode_number'],'frames':e['frames'],'seconds':e['seconds']} for e in d['episode_checks'] if e['short_candidate']]
    issues=[{'episode_index':e['episode_index'],'issues':e['issues']} for e in d['episode_checks'] if e['issues']]
    compact[color]={'episodes':d['episodes'],'frames':d['frames'],'task_exact':d['all_tasks_exact'],'format_issues':d['issues'],'episode_issues':issues,'short_candidates':short,'repeat_candidates':repeats,'cameras':camera_totals,'timestamp_bad_intervals':sum(e['timestamp_bad_intervals'] for e in d['episode_checks']),'nonfinite_values':sum(e['nonfinite_values'] for e in d['episode_checks'])}
lines += ['', '### 에피소드 후보 목록', '']
for color,c in compact.items():
    lines.append(f"- {color}: 짧음 {len(c['short_candidates'])}건, 1초 이상 동일 프레임 run {len(c['repeat_candidates'])}건, timestamp 간격 이상 {c['timestamp_bad_intervals']}개, NaN/Inf {c['nonfinite_values']}개.")
    for r in c['repeat_candidates']:
        lines.append(f"  - 순서 {r['user_episode_number']} / index {r['episode_index']}, {r['camera']}, {r['run_frames']}프레임({r['run_seconds']:.2f}초) 동일.")
    for r in c['short_candidates']:
        lines.append(f"  - 짧음: 순서 {r['user_episode_number']} / index {r['episode_index']}, {r['frames']}프레임({r['seconds']:.2f}초).")
    for r in c['episode_issues']:
        lines.append(f"  - index {r['episode_index']}: {', '.join(r['issues'])}")
lines += ['', '## 전체 에피소드 길이 대조', '', '| 색 | 사용자 순서 | index | 프레임 | 초 | 제외 여부 |', '|---|---:|---:|---:|---:|---|']
for color,d in datasets.items():
    for ep in d['episode_checks']:
        excluded=ep['episode_index'] in exclusions[color]['excluded_episodes']
        lines.append(f"| {color} | {ep['user_episode_number']} | {ep['episode_index']} | {ep['frames']} | {ep['seconds']:.2f} | {'사용자 지정 제외' if excluded else '포함'} |")
total_eps=sum(d['episodes'] for d in datasets.values());total_frames=sum(d['frames'] for d in datasets.values())
kept_eps=sum(len(x['included_episodes']) for x in exclusions.values());kept_frames=sum(x['remaining_frames'] for x in exclusions.values())
lines += ['', f'총 {total_eps}회 / {total_frames:,}프레임. 제외 목록을 적용하면 {kept_eps}회 / {kept_frames:,}프레임. 원본은 실제로 제외·변환·삭제하지 않았고 목록만 생성했다.', '',
          '출력: excluded_episodes.json(사용자 메모에 따른 제외/포함 번호), audit_red.json / audit_green.json / audit_blue.json(에피소드별 상세), summary.json(전체), compact_summary.json(요약).', '',
          '미실행: 원본 변경/삭제/덮어쓰기, 파생 데이터셋 생성, 카메라·serial 열기, 녹화, 학습, 로봇 구동, push. 로봇 성공 가능성은 판단하지 않았다.']
with (out/'DATA_CHECK.md').open('x',encoding='utf-8') as f:f.write('\n'.join(lines)+'\n')
with (out/'compact_summary.json').open('x',encoding='utf-8') as f:json.dump({'datasets':compact,'total_episodes':total_eps,'total_frames':total_frames,'kept_episodes':kept_eps,'kept_frames':kept_frames},f,ensure_ascii=False,indent=2)
print(json.dumps({'report':str(out/'DATA_CHECK.md'),'totals':{'episodes':total_eps,'frames':total_frames,'kept_episodes':kept_eps,'kept_frames':kept_frames},'datasets':compact}))

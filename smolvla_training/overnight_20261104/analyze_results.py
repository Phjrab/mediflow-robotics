"""이미 저장한 예측 CSV만 집계한다. 추론·장치 접근 없음."""
from collections import defaultdict
import csv
import json
from pathlib import Path
import statistics
from artifacts import save_json,save_csv,save_text
from evaluate_offline import limit_chunk
import policy_api as api

ROOT=Path(__file__).resolve().parent
def read_csv(path):return list(csv.DictReader(path.open(encoding='utf-8',newline='')))
def main():
    folder=ROOT/'reports/offline_eval_v2';summary=json.loads((folder/'summary.json').read_text())
    metrics=read_csv(folder/'joint_errors.csv');groups=defaultdict(list)
    for r in metrics:groups[(r['color'],r['phase'],r['joint'])].append(r)
    aggregated=[]
    for (color,phase,joint),rows in sorted(groups.items()):
        aggregated.append(dict(color=color,phase=phase,joint=joint,episodes=len(rows),
            mae=statistics.fmean(float(r['mae']) for r in rows),max_abs_error=max(float(r['max_abs_error']) for r in rows),
            unit='percent_0_100' if joint=='gripper' else 'degree'))
    save_csv(folder/'aggregated_errors.csv',aggregated)
    language=read_csv(folder/'language_effect.csv');pairs=defaultdict(list)
    for r in language:pairs[(r['sample'],r['pair'])].append(r)
    language_summary=[]
    for (sample,pair),rows in sorted(pairs.items()):
        body=[r for r in rows if r['joint']!='gripper'];grip=next(r for r in rows if r['joint']=='gripper')
        language_summary.append(dict(sample=sample,pair=pair,body_mean_abs_delta=statistics.fmean(float(r['mean_abs_delta']) for r in body),
            body_max_abs_delta=max(float(r['max_abs_delta']) for r in body),gripper_mean_abs_delta=float(grip['mean_abs_delta']),gripper_max_abs_delta=float(grip['max_abs_delta'])))
    save_csv(folder/'language_summary.csv',language_summary)
    samples=json.loads((ROOT/'reports/offline_samples/manifest.json').read_text())['samples']
    predicted=defaultdict(dict)
    for r in read_csv(folder/'action_chunks.csv'):predicted[r['sample']][(int(r['step']),r['joint'])]=float(r['predicted'])
    limits=[]
    for sample in samples:
        values=predicted[sample['id']]
        if len(values)!=300:raise ValueError('예측 chunk 행 누락')
        actions=[[values[(step,j)] for j in api.JOINTS] for step in range(50)]
        limits.append(dict(sample=sample['id'],**limit_chunk(actions,sample['state'])))
    save_json(folder/'chunk_limits_corrected.json',limits)
    camera=json.loads((ROOT/'reports/offline_samples/camera_comparison_metrics.json').read_text())
    lat=read_csv(folder/'latency.csv')
    report=['# 오프라인 평가 결과','',
        '**학습에 포함된 원본9회차의 재현 오차이며 held-out 성능이나 로봇 성공률이 아니다.** 토크ON 중단 뒤에는 로봇/serial에 재접근하지 않았다. 현재 카메라+현재 관절값 예측과 live는 BLOCKED/NOT_RUN이다.','',
        '## 실행 범위','',
        '- red/green/blue 각3회차, 회차마다 시작과중간: 총18표본. 각표본의50×6 예측 전체를 같은 시점부터의 녹화 action50개와 대조했다.',
        '- 녹화 영상 시각은 에피소드 video from_timestamp + 상대 data timestamp로 찾고 오차1.1프레임 이하를 확인했다.',
        '- 원본 제외 목록을 적용해 표본을 골랐으며 원본을 수정하지 않았다. 표본 manifest에 원본/에피소드/시각을 기록했다.',
        '- 동일 입력 지연12회와 동일 입력·동일seed101의 task조건 비교2시점을 실행했다.','',
        '## 지연과 메모리','',
        f"- 모델/processor 로딩: {summary['model_load_seconds']:.3f}초.",
        f"- 첫 회 {summary['latency_first']:.6f}초 /12회평균 {summary['latency_mean']:.6f}초 /첫회제외평균 {summary['latency_warm_mean']:.6f}초 /최대 {summary['latency_max']:.6f}초.",
        f"- dtype {summary['dtype']}. PyTorch GPU peak {summary['gpu_peak_bytes']/(1024**3):.3f}GiB, 프로세스RSS {summary['rss_bytes']/(1024**3):.3f}GiB, 관측시 시스템 가용RAM {summary['system_available_bytes']/(1024**3):.3f}GiB.",
        '- Orin은 공유 메모리이므로 GPU계수와RSS를 더해 전체 물리 메모리라고 계산하지 않는다. CUDA외할당/context는 torch peak계수와 다를 수 있다.',
        '- 지연은 저장된 입력을 사용한 전처리→정책→후처리→GPU동기화 시간이며 카메라 획득은 포함하지 않는다. 첫 회는 별도 표시했다.',
        '- Jetson 열 센서 마지막 값: '+json.dumps(summary['jetson_temperatures'],ensure_ascii=False)+'. 모터 Present_Temperature는 읽지 못해 UNVERIFIED이며 이 값으로 대체하지 않는다.','',
        '## 관절별 오차','',
        '| 색 | 구간 | 관절 | 회차 | MAE | 최대절대오차 | 단위 |','|---|---|---|---:|---:|---:|---|']
    for r in aggregated:report.append(f"| {r['color']} | {r['phase']} | {r['joint']} | {r['episodes']} | {r['mae']:.4f} | {r['max_abs_error']:.4f} | {r['unit']} |")
    report+=['','시작50프레임에는 정지/접근 준비 구간이 들어갈 수 있어 낮은 MAE가 과제 수행 능력을 보증하지 않는다. 중간 구간을 별도로 보고하고, 각회차 전체 결과는 joint_errors.csv/action_chunks.csv에 남겼다.','',
        '## 언어 조건 반응','',
        '같은 영상·state에서 task문구만 바꾸고 매번 seed101로 재설정했다. red반복은 같은 문구/같은seed의 대조군이다. 수치 차이는 언어조건에 반응한다는 관측일 뿐 올바른 바구니 선택 증거가 아니다.','',
        '| 표본 | task쌍 | 몸체평균차이(도) | 몸체최대차이(도) | 그리퍼평균차이(pp) | 그리퍼최대차이(pp) |','|---|---|---:|---:|---:|---:|']
    for r in language_summary:report.append(f"| {r['sample']} | {r['pair']} | {r['body_mean_abs_delta']:.6f} | {r['body_max_abs_delta']:.6f} | {r['gripper_mean_abs_delta']:.6f} | {r['gripper_max_abs_delta']:.6f} |")
    report+=['','## 50step 실행 시간과 제한 계산','',
        '-30Hz에서50개목표의 명목 스케줄은50/30=1.667초다.0.5도/step은15도/초이므로20도/초 상한보다 먼저 제한한다. 그리퍼는기존규칙2퍼센트포인트/step이다.',
        f"- 동기 모델이 첫회제외평균{summary['latency_warm_mean']:.3f}초 추론하고1.667초 전송한다면 한주기는명목{summary['latency_warm_mean']+50/30:.3f}초이며, 실제관측제어속도는NOT_RUN이다.",
        '-clip계산은 측정관절이 직전전송목표를 완벽히 따른다는 가정의 순수 계산이다. 실제지연·부하·접촉·관절한계는 검증하지 않았다. 모든원래목표에 도달했다는 뜻도 아니다.',
        '| 표본 | clip된step/50 | 명목전송시간(초) | 계산상태 |','|---|---:|---:|---|']
    for r in limits:report.append(f"| {r['sample']} | {r['clip_steps']} | {r['nominal_execution_seconds']:.3f} | {'거부' if 'simulation_blocked_step' in r else '가정하 계산'} |")
    report+=['','최초 자동산출 chunk_limits.json에는 NumPy scalar형 입력을 Python float로 변환하지 않아 제한 계산이 거부된 항목이 있었다. 입력 변환을 수정하고 저장 예측CSV로만 다시 계산한 chunk_limits_corrected.json을 최종값으로 사용한다. 모델 추론을 다시 돌리거나 원래 결과를 덮어쓰지 않았다.','',
        '## 카메라 비교','',
        'camera_comparison.png의 열1/2/3은 빨강/초록/파랑 학습시작 표본, 열4는 현재 영상이다. 행은camera1=손목, camera2=Astra수직, camera3=RealSense사선이다.','',
        '| 카메라 | 열/출처 | RGB전체평균(0~255) | 검정비율 |','|---|---|---:|---:|']
    for r in camera:report.append(f"| {r['camera']} | {r['column']} / {r['source']} | {r['brightness_rgb_mean']:.4f} | {100*r['black_fraction']:.4f}% |")
    report+=['','현재 영상은 매우 어두워 약통/바구니/집게와 구도 일치를 신뢰성 있게 확인하기 어렵다. 원인이 조명·노출·가림 중 무엇인지는 UNVERIFIED이다. 설정·조명을 임의로 바꾸거나 Astra 브리지를 재시작하지 않았다. 키 매핑과 해상도는 학습 설정과 일치하지만 장면 품질은 통과로 처리하지 않는다.','']
    save_text(folder/'EVALUATION_RESULTS.md','\n'.join(report))
    save_json(folder/'analysis_summary.json',dict(latency=summary,language=language_summary,limits=limits,camera=camera))
    print(json.dumps(dict(samples=len(samples),error_rows=len(metrics),language_cases=len(pairs),
        clip_steps=[r['clip_steps'] for r in limits],aggregated=aggregated),ensure_ascii=False),flush=True)

if __name__=='__main__':main()

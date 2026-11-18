# 모델 수신 완료 — 2026-11-04

- ZIP: 다운로드 폴더의 smolvla_three_color_020000.zip (1,197,824,770 bytes).
- ZIP SHA256 지정값 일치, 사전 수신한 체크포인트 영수증과 원본 7개 파일 크기·SHA256 모두 일치.
- 압축 해제 위치: .local/models/smolvla_three_color_020000_20261104/pretrained_model (프로젝트 루트 기준).
- VLM/토크나이저 캐시는 다운로드 폴더에서 찾지 못함. ZIP에도 없음. Jetson 캐시 유무는 미확인. 없으면 별도 전달 또는 승인된 다운로드가 필요함.
- Jetson 설치·전송·추론, 카메라·serial·로봇 실행은 이번 작업에서 하지 않음.

## Jetson으로 전송할 때 (Windows PowerShell)
아래는 준비 명령이며 아직 실행하지 않았다. 대상에는 ssh 단어 없이 계정@주소만 입력한다. 비밀번호는 ssh/scp 프롬프트에서만 입력한다.

```powershell
$taskJetsonTarget = Read-Host 'Jetson 계정@주소 (ssh 제외)'
scp -o ConnectTimeout=8 "$env:USERPROFILE/Downloads/smolvla_three_color_020000.zip" "${taskJetsonTarget}:smolvla_three_color_020000.zip"
```

Jetson에서 zip을 별도 새 폴더에 풀고, 신뢰된 검증 도구로 원본 7개 해시를 확인한다. 같은 이름 폴더가 있으면 덮어쓰지 않는다. 그 다음에는 장치 접근 없이 설치 환경·캐시·정책 로딩부터 확인한다. 첨부 실행기는 mock 전용이므로 live 명령을 바로 실행하지 않는다.

재개 입력: "Jetson으로 모델 전송하고 장치 없이 로딩 준비 이어서 해줘". 이 문구만으로 로봇 실행을 승인하지 않는다.

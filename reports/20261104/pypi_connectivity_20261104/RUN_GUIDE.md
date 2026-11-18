# 사용자가 Jetson에서 실행할 단계별 명령

아직 실행하지 않은 명령이다. 사용자가 직접 핫스팟 등 다른 네트워크로 연결한 다음 1단계부터 실행한다. 스크립트 생성은 새 파일에만 허용한다. 같은 이름이 이미 있으면 덮어쓰지 않고 중단한다.

## 1단계 — 파일 생성과 연결 검사만
```bash
(
set -e
set -C
cat > "$HOME/jetson_netfix_v1.sh" <<'EOF'
#!/usr/bin/env bash
# 사용자가 Jetson에서 직접 실행. 기본은 연결 검사만.
set -euo pipefail
mode=${1:-check}
case "$mode" in check|install) ;; *) echo '사용법: bash 스크립트 check 또는 install'; exit 2;; esac
envdir="$HOME/venvs/smolvla-py312"
py="$envdir/bin/python"
oldlib="$HOME/smolvla-cusparselt-W9m8A2/libcusparse_lt-linux-aarch64-0.6.3.2-archive/lib"

echo '[1] 공식 배포처 연결 검사'
for url in https://pypi.org/simple/filelock/ https://files.pythonhosted.org/; do
    curl -4 -sS -I --connect-timeout 8 --max-time 15 -o /dev/null \
        -w 'HTTP=%{http_code} TCP=%{time_connect} TLS=%{time_appconnect}\n' "$url"
done
echo '연결 검사 완료'
if [ "$mode" = check ]; then exit 0; fi

test -x "$py"
"$py" -B -c 'import sys; assert sys.version_info[:2] == (3, 12); print("Python 3.12 확인")'
echo '[2] 새 환경에만 PyTorch 설치'
"$py" -m pip --isolated install --retries 0 --timeout 30 \
    --index-url https://download.pytorch.org/whl/cu126 \
    'torch==2.10.0+cu126' 'torchvision==0.25.0+cu126'

# 새 torch는 cuSPARSELt 0.7.1을 요구한다. 0.6.3.2를 우선 로드하지 않는다.
newlib="$envdir/lib/python3.12/site-packages/nvidia/cusparselt/lib"
test -d "$newlib"
export LD_LIBRARY_PATH="$newlib:$oldlib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONDONTWRITEBYTECODE=1
echo '[3] 새 환경 GPU 계산 검사'
"$py" -B -c 'import torch,torchvision; print(torch.__version__,torchvision.__version__); assert torch.cuda.is_available(); x=torch.ones(4,device="cuda"); y=(x+x).sum(); torch.cuda.synchronize(); assert y.item()==8; print("GPU 계산 통과",torch.cuda.get_device_name(0))'

echo '[4] 새 환경에 SmolVLA 의존성 설치'
command -v git >/dev/null
"$py" -m pip --isolated install --retries 0 --timeout 30 \
    --index-url https://pypi.org/simple \
    --extra-index-url https://download.pytorch.org/whl/cu126 \
    'torch==2.10.0+cu126' 'torchvision==0.25.0+cu126' \
    'lerobot[smolvla] @ git+https://github.com/huggingface/lerobot.git@30da8e687a6dfc617fcd94afc367ac7071c376ce' \
    'transformers==5.5.4' 'accelerate==1.15.0' psutil
"$py" -m pip --isolated check
"$py" -B -c 'from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy; print("SmolVLA 불러오기 통과")'
echo '설치 단계 완료. 모델·카메라·로봇은 실행하지 않았습니다.'
EOF
bash "$HOME/jetson_netfix_v1.sh" check
)
```

연결 검사 완료가 나오면 2단계를 실행한다. 시간 초과/오류라면 중단하고 출력 전문을 가져온다. 이 스크립트는 기본 연결 검사에서 패키지를 설치하지 않는다.

## 2단계 — 설치 및 GPU 계산/import 검사
```bash
bash "$HOME/jetson_netfix_v1.sh" install
```

위 단계는 다시 연결을 검사한 다음 기존 smolvla-py312에만 설치한다. 각 단계 시작과 성공 결과가 출력된다. set -euo pipefail로 오류 시 중단하고 별도 로그로 오류를 숨기지 않는다. 새 환경에 일부 패키지가 설치된 상태에서 실패할 수 있으나 LeLab/모델 원본은 바뀌지 않는다. conda 환경을 재생성하지 않는다.

## 3단계 — 사용자 출력 확인 후 진행
설치/import까지 통과한 출력을 Codex에 전달한다. 토크나이저 캐시 준비와 실제 모델·가짜 입력 검사는 그 다음 단계다. 이번 스크립트에 모델 로딩/로봇/카메라/serial 기능은 없다.

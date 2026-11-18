# PyPI 접속 장애 조사 및 설치 재개안 — 2026-11-04

## 실제 수행한 명령과 결과
설치·SSH·장치 접근 없음. 공유기/DNS/네트워크 설정 변경 없음.

PC 조회: Python socket.getaddrinfo(host,443,AF_INET/AF_INET6), 공개 서비스 이름만 사용.
PC 접속: curl.exe -q -4/-6 -sS -I --connect-timeout 6 --max-time 10 -o NUL -w <HTTP/DNS/TCP/TLS/시간> https://<공식호스트>/
PyPI만 /simple/ 조회. 대조 호스트는 루트 조회. probe_pc.py가 실제 명령을 기록한다.

초기 샌드박스 실행은 모든 연결이 즉시 실패했으므로 실제 Wi-Fi 판정에 사용하지 않았다. 승인된 샌드박스 밖 읽기 전용 검사 결과만 pc_network_attempts.json에 저장했다. dns.json은 로컬 이름 조회 결과다.

| 대상 | IPv4 실제 결과 | IPv6 실제 결과 |
|---|---|---|
| pypi.org | 총3회 curl 28, TCP 연결 시간 0, TLS 시간 0 | curl 6, 이름 조회 실패 |
| files.pythonhosted.org | 총3회 curl 28, TCP 연결 시간 0, TLS 시간 0 | curl 6, 이름 조회 실패 |
| download.pytorch.org | TCP/TLS 성공, 루트 HTTP403. 실제 휠 메타데이터 GET은 성공 | curl 6 |
| github.com | HTTP200, TCP/TLS 성공 | curl 6 |
| huggingface.co | HTTP200, TCP/TLS 성공 | curl 6 |
| conda.anaconda.org | HTTP302, TCP/TLS 성공. 전체 패키지 다운로드 보장은 아님 | curl 6 |

두 PyPI 호스트의 IPv4: 151.101.0.223 / 151.101.64.223 / 151.101.128.223 / 151.101.192.223.
IPv6 주소를 현재 로컬 조회에서 받지 못했다. IPv6 인터넷 전체가 불가능하다고 단정하지 않는다.
재시도: 1회 종료 후 15초 대기, 총3회. 각각 연결 제한6초/전체10초. 동일 결과였다. 최초 시작부터 마지막 시작까지 약42초, 관측 종료까지 약48초다. 짧은 관측이므로 장기 장애/전 세계 장애로 판정하지 않는다.

판정: IPv4 DNS 응답 이후 TCP/443 연결 단계의 장애. TLS 인증서 오류나 패키지 버전 오류가 아니다. 이 Wi-Fi 두 장비의 사용자 관측과 맞지만 공유기·ISP·방화벽·CDN 경로 중 정확한 책임 구간은 미확인. DNS 설정 변경은 하지 않았다.

## 우선순위와 추천
1. 제한된 재시도: 실제3회 수행, 모두 실패. 무한 재시도하지 않는다.
2. 추천: 사용자가 직접 휴대폰 핫스팟 등 다른 네트워크에 연결한 뒤 check 단계만 실행. PyPI 두 호스트가 응답하면 기존에 만든 smolvla-py312 환경에서 install 단계만 재개한다. 환경 재생성/LeLab 변경이 없고 의존성을 정상 해결할 수 있어 가장 간단하다. 핫스팟 실제 연결성과 데이터 사용량은 미확인. 휠 전체는 수GB 이상일 수 있다.
3. 현재 접속되는 출처만 사용: torch/torchvision 휠 및 LeRobot 소스는 받을 수 있지만 PyTorch 인덱스도 filelock 등을 files.pythonhosted.org로 연결한다. 단순 --index-url 변경은 실패가 재현되는 경로다. conda-forge로 일부 의존성을 대체할 가능성은 있지만 정확한 모든 버전·ARM64·CUDA 구성의 완전 해결은 NOT_RUN. 일반 conda pytorch가 요구 CUDA 빌드라고 가정하지 않는다. Hugging Face 임의 사용자 저장소나 비공식 미러의 휠로 우회하지 않는다.
4. 핫스팟도 불가하면 온라인 Linux ARM64/Python3.12 환경에서 오프라인 묶음을 생성해 USB로 옮긴다. 다른 PC가 Windows/x86이면 native ARM64 장비 또는 공식 ARM64 Linux 컨테이너의 Python3.12가 필요하다. 호스트와 타깃의 의존성 조건 해석 차이를 피하려고 --platform 옵션만 바꾼 Windows 다운로드 절차는 제공하지 않는다. 컨테이너 설치/실행은 NOT_RUN.

## 패키지별 출처와 확인 범위
- torch 2.10.0+cu126 / torchvision 0.25.0+cu126: download.pytorch.org 공식 ARM64 cp312 휠. 작은 METADATA 실제 다운로드 성공. GPU에서의 실제 동작은 NOT_RUN.
- LeRobot: github.com/huggingface/lerobot 커밋30da8e687a6dfc617fcd94afc367ac7071c376ce. 정확한 pyproject 실제 다운로드 성공. 의존성/빌드 도구는 별도로 필요.
- filelock: PyTorch 인덱스의253개 링크가 files.pythonhosted.org를 가리키는 것을 실제 확인. 인덱스 접근 성공과 파일 접근 성공은 다르다.
- transformers5.5.4 / accelerate1.15.0 / psutil / num2words 및 대부분 일반 Python 의존성: 표준 경로는 pypi.org의 인덱스와 files.pythonhosted.org의 파일. 공식 GitHub 소스로 빌드 가능한 프로젝트도 있으나 모든 전이 의존성의 빌드·휠 확보는 미확인. PyPI만이 유일한 배포처라고 단정하지 않는다.
- CUDA 관련 Python 패키지: torch 휠이 명시하는 nvidia-* 및 cuda-bindings, triton도 전부 필요. 시스템 CUDA가 있다는 이유로 --no-deps 설치하지 않는다. 개별 휠 존재·최종 다운로드 호스트는 오프라인 해결 후 확정한다.
- VLM/토크나이저: huggingface.co/HuggingFaceTB/SmolVLM2-500M-Video-Instruct revision7b375e1b73b11138ff12fe22c8f2822d8fe03467. 접속 가능한 공식 모델 저장소지만 이번 패키지 묶음에는 포함하지 않는다. 모델 로딩 전 별도 캐시 준비 필요. 학습 완료 체크포인트는 재다운로드/재학습하지 않는다.

중요 보완: 새 torch 휠은 nvidia-cusparselt-cu12==0.7.1을 명시한다. 기존 torch2.8에서 검증한 라이브러리0.6.3.2를 새 torch보다 먼저 강제 로드하면 ABI 문제가 생길 수 있다. 설치 스크립트는 새 환경의 nvidia/cusparselt/lib를 먼저, 사용자 지정 기존 라이브러리 경로를 뒤에 둔다. 셸 한 프로세스에만 적용하고 시스템 환경/기존 LeLab은 바꾸지 않는다.

## 의존 패키지 목록
직접 요구사항48개는 verified_direct_dependencies.json에 공식 METADATA/pyproject 근거와 함께 저장했다. 아래는 해당 원문 목록이다. extra 없는 기본/SmolVLA 선택 항목만 포함하며, 중복도 근거 보존을 위해 유지한다.

- torch: `filelock`
- torch: `typing-extensions>=4.10.0`
- torch: `setuptools; python_version >= "3.12"`
- torch: `sympy>=1.13.3`
- torch: `networkx>=2.5.1`
- torch: `jinja2`
- torch: `fsspec>=0.8.5`
- torch: `cuda-bindings==12.9.4; platform_system == "Linux"`
- torch: `nvidia-cuda-nvrtc-cu12==12.6.77; platform_system == "Linux"`
- torch: `nvidia-cuda-runtime-cu12==12.6.77; platform_system == "Linux"`
- torch: `nvidia-cuda-cupti-cu12==12.6.80; platform_system == "Linux"`
- torch: `nvidia-cudnn-cu12==9.10.2.21; platform_system == "Linux"`
- torch: `nvidia-cublas-cu12==12.6.4.1; platform_system == "Linux"`
- torch: `nvidia-cufft-cu12==11.3.0.4; platform_system == "Linux"`
- torch: `nvidia-curand-cu12==10.3.7.77; platform_system == "Linux"`
- torch: `nvidia-cusolver-cu12==11.7.1.2; platform_system == "Linux"`
- torch: `nvidia-cusparse-cu12==12.5.4.2; platform_system == "Linux"`
- torch: `nvidia-cusparselt-cu12==0.7.1; platform_system == "Linux"`
- torch: `nvidia-nccl-cu12==2.27.5; platform_system == "Linux"`
- torch: `nvidia-nvshmem-cu12==3.4.5; platform_system == "Linux"`
- torch: `nvidia-nvtx-cu12==12.6.77; platform_system == "Linux"`
- torch: `nvidia-nvjitlink-cu12==12.6.85; platform_system == "Linux"`
- torch: `nvidia-cufile-cu12==1.11.1.6; platform_system == "Linux"`
- torch: `triton==3.6.0; platform_system == "Linux"`
- torchvision: `numpy`
- torchvision: `torch (==2.10.0)`
- torchvision: `pillow (!=8.3.*,>=5.3.0)`
- lerobot: `torch>=2.7,<2.12.0`
- lerobot: `torchvision>=0.22.0,<0.27.0`
- lerobot: `numpy>=2.0.0,<2.3.0`
- lerobot: `opencv-python-headless>=4.9.0,<4.14.0`
- lerobot: `Pillow>=10.0.0,<13.0.0`
- lerobot: `einops>=0.8.0,<0.9.0`
- lerobot: `draccus==0.10.0`
- lerobot: `huggingface-hub>=1.0.0,<2.0.0`
- lerobot: `requests>=2.32.0,<3.0.0`
- lerobot: `gymnasium>=1.1.1,<2.0.0`
- lerobot: `safetensors>=0.4.3,<1.0.0`
- lerobot: `packaging>=24.2,<26.0`
- lerobot: `termcolor>=2.4.0,<4.0.0`
- lerobot: `tqdm>=4.66.0,<5.0.0`
- lerobot: `cmake>=3.29.0.1,<4.2.0`
- lerobot: `setuptools>=71.0.0,<81.0.0`
- lerobot[smolvla]: `lerobot[transformers-dep]`
- lerobot[smolvla]: `num2words>=0.5.14,<0.6.0`
- lerobot[smolvla]: `lerobot[accelerate-dep]`
- lerobot[transformers-dep]: `transformers>=5.4.0,<5.6.0`
- lerobot[accelerate-dep]: `accelerate>=1.14.0,<2.0.0`

**최종 전이 의존성 전체의 버전 고정 목록은 미확인**이다. PyPI 연결 차단으로 해석기를 완료하지 못했으므로 이전 학습 PC의 Windows freeze를 Jetson 의존성 전체라고 제시하지 않는다. build_offline_bundle.py를 온라인 Linux ARM64/CPython3.12에서 실행하면 다음을 실제 생성한다.
- wheel_manifest.json: 최종 패키지명·버전·모든 Requires-Dist·파일명·크기·SHA256 전체.
- requirements-offline.txt: 모든 전이 의존성의 버전과 --hash 고정 목록.
- SHA256SUMS: 파일별 전송 후 검증용 목록.
- offline_resolution.json: --no-index --dry-run으로 전체 의존성 해결 확인.
- READY.json: 위 과정이 모두 완료된 경우에만 생성. 실패하면 생성하지 않음.

소스 전용: 정확한 LeRobot Git 커밋은 온라인 준비 단계에서 wheel로 빌드한다. num2words가 쓰는 docopt0.6.2는 공식 PyPI에 sdist만 있으므로 준비 단계에서 공식 소스를 로컬 wheel로 빌드한다. 이 둘에만 빌드를 명시적으로 허용하고 나머지는 --only-binary=:all:로 받는다. 다른 소스 전용/ARM64 휠 누락이 있으면 해당 패키지 이름이 나온 오류에서 멈춘다. docopt 휠을 빌드했으므로 소스 전용이라는 이유만으로 전체 오프라인 설치가 불가능한 것은 아니다. 추가 소스 전용 패키지 목록은 해석 미완료로 미확인이다.

실행(온라인 Linux ARM64/Python3.12, 새 출력 경로):

```bash
python3.12 build_offline_bundle.py --out "$HOME/smolvla-offline-20261104"
```

위 준비 도구 자체는 환경에 패키지를 설치하지 않고 wheel 빌드·다운로드·설치 dry-run만 한다. pip의 임시 빌드 환경은 사용한다. 준비 PC의 git과 pip가 필요하다. 이미 존재하는 출력 경로는 거부한다. 실패 시 자동으로 패키지 버전을 바꾸거나 비공식 바이너리를 찾지 않는다.
READY.json이 있는 묶음을 통째로 USB로 Jetson에 옮긴 뒤:

```bash
bash install_offline_jetson.sh "$HOME/smolvla-offline-20261104"
```

실행기는 sha256sum -c로 파일 전부를 검증한 뒤 지정된 새 환경에만 --no-index --require-hashes로 설치한다. 준비 묶음/원본 모델/LeLab에 쓰지 않는다. 이후 GPU 계산·SmolVLA import만 확인하며 모델·카메라·serial은 열지 않는다.

## 검증과 미실행
로컬 도구 검사: Python 문법2개 통과, 합성 휠의 메타데이터/해시 목록 검사1개 통과, bash -n 설치 스크립트2개 모두 exit0. 실제 설치 성공을 뜻하지 않는다.
실제 수행: PC DNS/HTTPS 검사, 공식 작은 메타데이터 읽기, 보고서/도구 작성.
NOT_RUN: Jetson SSH, 핫스팟 연결/검사, Jetson 설치, conda 대체 전체 해석, 오프라인 휠 전체 준비, 최종 전체 의존성 해석, 모델 추론, 하드웨어 접근, push.

## 근거
- https://download.pytorch.org/whl/cu126/filelock/
- https://download.pytorch.org/whl/cu126/torch-2.10.0%2Bcu126-cp312-cp312-manylinux_2_28_aarch64.whl.metadata
- https://download.pytorch.org/whl/cu126/torchvision-0.25.0%2Bcu126-cp312-cp312-manylinux_2_28_aarch64.whl.metadata
- https://raw.githubusercontent.com/huggingface/lerobot/30da8e687a6dfc617fcd94afc367ac7071c376ce/pyproject.toml
- https://pypi.org/pypi/docopt/0.6.2/json
- https://pip.pypa.io/en/stable/cli/pip_download/
- https://pip.pypa.io/en/stable/cli/pip_install/

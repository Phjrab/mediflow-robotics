# STS3215 온도 스파이크 외부 1차 자료 조사

조사일: 2026-11-11, KST. 범위: 원저자 GitHub 저장소/코드/이슈 및 연결 문서 읽기. 변경파일은 이 보고서뿐이다. 런타임·모터·FW·보정 쓰기, SSH·장치 실행은 하지 않았다.

## 결론

공개 원저자 자료에서 STS3215 계열의 ADC 온도 변환 경로와 커스텀 펌웨어의 순간 온도 이상/보호 한계는 확인했다. 그러나 설치된 공장 FW 3.10이 이번 60→38→57, 토크 OFF 독립 raw 읽기 36→53/34ms를 만드는 정확한 버그와, 그 버그를 고쳤다고 검증된 패치는 확인하지 못했다. 다른 펌웨어의 문제 설명이나 테스트 통과를 우리 실기 원인 확정·해결 증거로 쓸 수 없다.

현장 사실은 총괄 전달 기록에 따른다: model777=[9,3], FW3.10, SDK 없는 독립 raw 읽기/토크 OFF에서도 짧은 정상-checksum 온도 스파이크가 존재한다. 센서/ADC·변환/장치 전원·참조전압/모터 펌웨어/전기통신 경로가 남아 있다. 온도 평균화·다중 필터·높은 값 무시·임계 상향은 이번 조사에서 해결책으로 제안하지 않는다.

## 확인한 1차 자료

### 1. 0o8o0-blip/sts3215-firmware의 성격과 적용 한계

저장소는 Feetech 공식 소스가 아니라 GD32F130C6T6용 clean-room 재구현이다. README는 adc.c를 ADC/온도 선형화 구현으로 안내하고, 온도 레지스터63을 보드 섭씨 온도로 설명한다. 저자의 하드웨어/차등 테스트 성공 주장은 그 재구현의 주장이지 우리 공장 이미지의 원문 검증은 아니다. README 블록도와 현재 실제 코드가 모든 획득 세부에서 같다고 전제하지 않아야 한다. [원저자 README](https://github.com/0o8o0-blip/sts3215-firmware#readme)

### 2. 실제 ADC 변환 코드에서 보이는 경로

adc.c:64–71은 선택된 ADC 버퍼 채널을 읽고 raw를2비트 시프트한 뒤 adc_linearize_temp에 넘긴다. :187–214는 LUT 검색과 구간 보간으로 uint8 온도를 만든다. adc_iir_filter 함수의 존재와 별개로, 현재 adc_read_temp 본문은 그 함수를 직접 호출하지 않는다. 따라서 주석의 IIR 설명만 보고 실제 필터가 적용된다고 단정하면 안 된다. 이는 ADC값/채널/LUT 상태가 온도 숫자 생성에 관여한다는 코드 증거다. 공장3.10의 스파이크가 특정 LUT 버그라는 증거는 아니다. [adc.c](https://github.com/0o8o0-blip/sts3215-firmware/blob/main/firmware/src/adc.c#L63)

### 3. ADC 획득 타이밍과 채널 전환은 조사할 가치가 있다

main.c는 대상 공장 바이너리를 SCServo21-GD32-TTL-250306이라고 적는다. 현재 구현 :591–660에는 PWM 타이머 ISR에서 온도CH1/전류CH3/전압CH4를 시간 분할하고, 이전 변환 EOC 잔여를 처리하며, 선택한 버퍼 슬롯에 결과를 넣는 코드가 있다. :696은 그 결과로 온도 레지스터를 갱신한다. 이는 변환 획득·채널/버퍼·타이밍을 확인할 위치를 제시한다. 이 커스텀 구현의 설명을 설치3.10 바이너리의 입증된 race/수정이라고 부르면 안 된다. [main.c](https://github.com/0o8o0-blip/sts3215-firmware/blob/main/firmware/src/main.c#L591)

별도 초기화 코드는 소프트웨어 트리거 변환과 버퍼 초기값 설정을 명시한다. 따라서 README의 DMA 도식만 인용하는 것보다 소스 획득 경로를 함께 봐야 한다. [i2c.c ADC 초기화](https://github.com/0o8o0-blip/sts3215-firmware/blob/main/firmware/src/i2c.c#L194)

### 4. Robo9 경고의 정확한 대상

requirements는 GD32F130 기반 서보, 선택된 v0.1-preview 이미지의 model bytes [9,3], Extension9.0을 명시한다. 같은 모델 바이트는 호환성 점검 조건이지 모든 공장 펌웨어/보드가 동일하다는 증거가 아니다. [Before you start](https://robonine.com/docs/sts32xx/start/requirements/)

연결된 release의 Known issues는 이동 중 온도 읽기/온도 보호 신뢰성 문제와 일부 밀접한 명령의 공장 동작 차이를 명시한다. **해당 커스텀 연구 preview의 한계**다. 설치 FW3.10의 공식 결함 공지나 검증된 해결 버전이라고 해석할 수 없다. [v0.1-preview 릴리즈](https://github.com/roboninecom/STS32XX-Firmware/releases/tag/v0.1-preview)

### 5. 화면 온도 필터가 보호 신뢰성 해결과 다른 이유

Robo9 상세 문서의 Current limits는 extension 표시 피드백에서 고립된 점프를 억제해도 모터 보호가 raw 센서를 사용해 짧은 이상 읽기에 영향을 받을 수 있다고 설명한다. 표시값을 다듬었다는 사실이 원인 수리나 안전 보호 회복을 뜻하지 않는다는 직접 근거다. 이 문서의 필터 구현을 우리에게 적용하자는 제안이 아니다. [Detailed documentation: Current limits](https://github.com/roboninecom/STS32XX-Firmware/blob/main/DETAILED_DOCUMENTATION.md#current-limits)

### 6. 원저자 진단 도구가 인정하는 증거의 한계

Robo9 diagnostics 문서는 정지·토크 OFF 텔레메트리에서 원문과 이상값을 기록하고, 연속 유효 응답으로 온도 스파이크를 식별한다. 동시에 그 플래그는 FW/센서 고장의 증명은 아니라고 명시한다. 응답에 sequence가 없어 timeout 후 버스 quiet를 기다린다는 설명도 있다. 이 설계는 원문·실패 보존과 수신 경계 진단을 뒷받침하지만 quiet 대기 자체가 이번 문제의 치료라는 근거는 아니다. 옵션 중 RAM 쓰기 benchmark도 있으므로 이 도구 전체를 검토 없이 그대로 실행할 근거로 삼지 않는다. [Diagnostics README](https://github.com/roboninecom/STS32XX-Firmware/blob/main/tools/diagnostics/README.md)

### 7. 3.9→3.10 관련 이슈와 이번 사례의 차이

LeRobot #1010 원작성자는 7.4V 모델을12V로 공급한 전압 경보와 FW3.9/3.10 문제를 보고한다. 이번 상태는 이미3.10, status0이며 토크 OFF 정상 프레임 스파이크라서 같은 사례라고 할 수 없다. 이 이슈는 모델 전압/이미지 식별의 중요성을 보여줄 뿐 업그레이드가 우리 온도 문제를 고친다는 근거는 아니다. [LeRobot issue #1010](https://github.com/huggingface/lerobot/issues/1010)

## 추정과 배제 범위

| 판단 | 현재 근거 수준 |
|---|---|
| Python offset/정수 변환만이 온도 이상을 생성 | 독립 raw 정상 프레임 payload 증거와 맞지 않음 |
| SDK·bulk·토크 ON·실제 움직임이 필수 원인 | OFF/SDK 없는 raw 재현으로 필수조건 가설은 배제 |
| 특정 공장3.10 ADC race가 확정 | 미확인. 외부 clean-room 코드는 조사 위치만 제시 |
| 센서/ADC·전원/참조·FW·버스 중 한 곳 확정 | 미확인. 호스트 RX의 정상checksum만으로 송신 이전/이후를 완전히 분리 못함 |
| custom9 preview 설치가 해결 | 근거 없음. 자체 온도/보호 한계 경고가 존재 |
| 온도 표시 필터로 안전 복구 | 근거 없음. 이상값 원인과 보호 신뢰성은 남음 |

## 총괄에게 전달할 조사 결과

다음 원인 분리를 위해 필요한 자료는 설치 이미지의 정확한 빌드/보드 정보, 실제 공급전압 및 온도센서/ADC 획득에 관한 제조사 답변, 가능한 경우 송신선 측 독립 원문 관측이다. 어떤 방법도 이 보고서가 추가 장치 동작·분해·FW 쓰기를 승인하는 것은 아니다. 현 상태에서 공급사에 제공할 재현 묶음은 OFF/READ-only, 정확 TX/RX, ID/모델/FW, 시간 간격, DC 재투입/재연결 전후, SDK 독립 재현, queue0와55 중단 유지 사실이다.

공장3.10 동일 결함의 공개 이슈/수정 커밋은 이번 검색 범위에서 찾지 못했다. 0o8o0 열린 이슈 화면은0건이었지만 전체 과거 이슈 부재를 증명하지 않는다. 일부 GitHub commit/issue 페이지는 조회 실패해 검색 범위의 한계로 남겼다. 검색은 GitHub STS3215 temperature spike/ADC/FW3.10 및 지정 원저자 저장소 중심이었다. [원저자 이슈 화면](https://github.com/0o8o0-blip/sts3215-firmware/issues)

자료는 main/release URL 기준의 조사 시점 내용이며 바뀔 수 있다. 이번 보고서의 결론은 **계측 경로 조사 근거는 확보했지만 실제 원인과 검증된 수리는 아직 미확정**이다.

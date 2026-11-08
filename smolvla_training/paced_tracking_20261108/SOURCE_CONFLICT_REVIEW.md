# 설정표 대조와 처리

설치된 LeRobot 파일 lerobot/motors/feetech/tables.py의 SHA256은71f7f7beb17169781bd26a33b165ed4c5c3df7b9c477d36860f9d6011eb00428이다. 필요한 줄은 REGISTER_SOURCE.json, REGISTER_EXTRA_SOURCE.json, REGISTER_FEEDBACK_SOURCE.json에 보존했다. 설치본은 수정하지 않았다.

SDK 표에는 Minimum_Startup_Force가 주소24·길이2로 정의돼 있다(62행). 공급사 ST 문서는24와25를 각1바이트로 구분한다. SDK의 Goal_Position_2는 주소71·길이2다(91행). 공급사 표는 목표 피드백을67로 기술하고71은 미정의로 둔다. 모델·펌웨어 차이인지 문서/SDK 차이인지는 미확인이다. [공급사 ST 메모리 표](https://docs.waveshare.com/Memory_Map_Explanation/ST_Servo_Memory_Map_Explanation).

새 읽기 진단은 충돌하는 SDK 이름을 자동 조회에서 제외하고,24와25는 각각 원시1바이트로 저장한다.67은 기존에 확인한 공급사 프로필에 따라 원시2바이트로 읽는다. 펌웨어·모델·엔디언 관련0~4도 원시값으로 저장한다. 이 값들은 설정 변경이나 단위 확정에 사용하지 않는다.

Goal_Velocity는 SDK 표80행에 주소46·길이2로 존재한다. 이전 후보 이름 Goal_Speed를 가정해 쓰지 않았다. 기동력·전압·Phase를 포함해 이름과 크기를 실행 전 설치 표에서 확인한다. SDK에 없는 선택 항목은 NOT_FOUND로 기록한다.

기존 live의67 피드백 프로필과 안전 계층은 이번에 바꾸지 않았다. 출처 차이를 이유로71로 치환하거나, 현재값을 목표값으로 간주하지 않았다. 실제 설정값과 펌웨어 정보는 아직 NOT_RUN이다.

# LeLab 리더/팔로워 포트 수정·홈 확인

2026-11-15. 오류는 `리더 포트가 확인한 고정 USB ID와 다릅니다.`였다. OS는리더5AE6085272→ttyACM1,팔로워5AE6058306→ttyACM0이었지만 LeLab의so-101 프로필과전역포트파일은이전번호(리더ACM0/팔로워ACM1)를저장하고있었다.

기존metadata파일을.bak로보존한뒤LeLab의저장API로다음과같이수정했다.

- 리더: `/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AE6085272-if00`
- 팔로워: `/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AE6058306-if00`
- 수정파일: `~/.cache/huggingface/lerobot/robots/so-101.json`, `ports/leader_port.txt`, `ports/follower_port.txt`.
- 기존시리얼검사는유지했다. 정상조합통과·반대조합거부를실제validate_ports함수로검증했다. 카메라·보정선택값은그대로다.
- follower보정파일SHA256은기존2f74c1303b2fce03576eff539329e1185a652e2d551caa1ec82b026dafe29c2e와일치한다. 설치코드·보정파일·VLA실행기변경은없다.

사용자가직접텔레옵을시작한뒤상태following·last_error없음을확인했다. 에이전트가텔레옵시작버튼/시작API를호출하지않았다. 같은`/ws/joint-data`를구독해6축메시지12개,약19.916Hz,최신표본나이0.043초를확인했다. 이관찰자는serial을별도로열거나모터쓰기를하지않았다. 사용자텔레옵자체의동작/모터쓰기는이관찰자의쓰기0과구분한다.

|축|현재|홈차이|틱|홈±2|
|---|---:|---:|---:|---|
|어깨회전|-3.65°|+0.09°|2060|안|
|어깨들기|-98.77°|-1.02°|831|안|
|팔꿈치|96.48°|-0.07°|3081|안|
|손목굽힘|41.89°|-1.57°|2557|안|
|손목회전|-3.65°|-1.40°|1823|안|
|그리퍼|4.90점|+0.62점|493|안|

홈6축허용폭및명령범위를확인했다.이는표본시점의위치확인이며지속중인텔레옵으로이후위치는달라질수있다. 홈완료후텔레옵정지버튼을누르도록안내했다. live는실행하지않았다.

```bash
bash ~/so101_vla/live/run.sh --live --speed 0.33
```

위명령은텔레옵을종료하고팔주변경로를확인한운영자가실행할명령이다. 온도급변원인조사와기존55°C보호는그대로다. 이작업은VLA집기성공판정이아니다.

원본: Jetson `~/so101_vla/reports/20261115/LELAB_PORT_FIX_1794726670285369865/`의 RESULT.json, GLOBAL_PORTS_RESULT.json, BROADCAST_CHECK.json, HOME_CHECK_1794726898293295354.json. 포트metadata백업은각원래파일옆.bak_20261115_161110/161335에있다. 이전오류기록은삭제하지않았다.

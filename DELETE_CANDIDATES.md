# 삭제 후보 — 아직 삭제하지 않음

1. archive 안의`__pycache__`, `.pyc`: 재생성 가능한 캐시.
2. 배포가 검증된`candidate.tar.gz`, `update_v2.tar.gz`, `final_patch.tar.gz`, `paths_patch.tar.gz` 등 중간 배포 묶음: 보관 소스와 Git 커밋을 대조한 뒤 검토.
3. `_archive/20261110/duplicate_modules/`, `unused_deps/`: 현행 import 경로에서 제외했지만 과거 재현에 필요할 수 있다.
4. Windows 권한 제한으로 남은 옛 저장소 잔여물: 전체 복사본과 Git bundle의 검증 기록을 확인한 뒤 소유자가 판단한다. 이번에는 권한을 바꾸거나 삭제하지 않았다.

보정·모델·데이터 원본, NOT_PUSHED Git bundle, 중단 원문, 출처·해시 기록은 삭제 후보에 넣지 않는다. 0스텝 실행도 중단 증거이므로 archive에 보존했다.

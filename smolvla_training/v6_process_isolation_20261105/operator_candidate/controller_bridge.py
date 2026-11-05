"""수정하지 않은 기존 RobotController를 로드한다. 패키지 서버 초기화는 하지 않는다."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import types

HASHES={"robot.py":"dec747b7beb900507275907e2ac8c6bfe87af7b90060c9a11d6d6d647298f19f",
        "models.py":"43a01159707e1141263f9dc483d031a1baa575b2d14b417c7a425b9917d78a49",
        "errors.py":"2ad67a2f652855dfefc0752b5817c33cbf84938685568f5559f495334882419c"}

def load_controller(*,mock_posix=False):
    root=Path(__file__).resolve().parent/"controller_source"
    for name,digest in HASHES.items():
        if hashlib.sha256((root/name).read_text(encoding="utf-8").encode("utf-8")).hexdigest()!=digest:
            raise RuntimeError("기존 controller 소스 해시 불일치")
    prefix="_first_trial_original_controller"
    if prefix+".robot" in sys.modules:return sys.modules[prefix+".robot"].RobotController
    package=types.ModuleType(prefix);package.__path__=[str(root)];sys.modules[prefix]=package
    replacements={}
    if os.name=="nt":
        if not mock_posix:raise RuntimeError("실기 controller는 Linux에서만 사용")
        def forbidden(*args,**kwargs):raise AssertionError("mock에서 POSIX 장치 접근 금지")
        for name in ("fcntl","termios"):
            replacements[name]=sys.modules.get(name)
            sys.modules[name]=types.SimpleNamespace(flock=forbidden,ioctl=forbidden,LOCK_EX=2,LOCK_NB=4,LOCK_UN=8,TIOCEXCL=0x540c)
    try:return importlib.import_module(prefix+".robot").RobotController
    finally:
        for name,original in replacements.items():
            if original is None:sys.modules.pop(name,None)
            else:sys.modules[name]=original

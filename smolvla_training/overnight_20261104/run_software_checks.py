"""이번 변경과 관련된 장치 없는 테스트만 선택해 결과를 보존한다."""
import ast
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from artifacts import save_json,save_text

ROOT=Path(__file__).resolve().parent
PATTERNS=('test_observation_contract.py','test_software_preflight.py','test_precision_benchmark.py',
          'test_cached_replay.py','test_mock_controller_integration.py','test_v5.py','test_preview_warmup.py',
          'test_lights_preview.py','test_dry.py','test_preflight.py','test_three_cameras.py','test_software_installer.py')


def main():
    started=time.perf_counter();loader=unittest.TestLoader();suite=unittest.TestSuite()
    for pattern in PATTERNS:suite.addTests(loader.discover(str(ROOT/'tests'),pattern=pattern))
    stream=io.StringIO();result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    for path in list(ROOT.glob('*.py'))+list((ROOT/'tests').glob('*.py')):
        ast.parse(path.read_text(encoding='utf-8'),filename=path.name,feature_version=(3,10))
    bash=Path('C:/Program Files/Git/bin/bash.exe') if sys.platform=='win32' else Path('/bin/bash')
    shells=sorted(ROOT.glob('*.sh'))
    for path in shells:subprocess.run([str(bash),'--noprofile','--norc','-n',str(path)],check=True)
    report=dict(tests_run=result.testsRun,passed=result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
                failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
                exit_code=0 if result.wasSuccessful() else 1,seconds=time.perf_counter()-started,patterns=list(PATTERNS),
                bash_syntax_files=len(shells),python310_syntax='PASS',hardware_execution='NOT_RUN',model_inference='NOT_RUN')
    save_text(ROOT/'reports/SOFTWARE_TESTS.txt',stream.getvalue());save_json(ROOT/'reports/SOFTWARE_TESTS.json',report)
    print(json.dumps(report,ensure_ascii=False));raise SystemExit(report['exit_code'])


if __name__=='__main__':main()

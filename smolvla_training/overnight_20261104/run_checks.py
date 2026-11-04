"""장치 없는 새 테스트와 문법 검사 결과만 보존한다."""
import ast
import io
import json
from pathlib import Path
import subprocess
import time
import unittest
from artifacts import save_json, save_text

root = Path(__file__).resolve().parent
started = time.monotonic()
parsed = []
for path in sorted(list(root.glob('*.py')) + list((root / 'tests').glob('*.py'))):
    ast.parse(path.read_text(encoding='utf-8'), filename=path.name, feature_version=(3, 10))
    parsed.append(str(path.relative_to(root)))
for name in ('nov_preflight.sh', 'nov_policy_run.sh'):
    subprocess.run(['bash', '-n', str(root / name)], check=True)
stream = io.StringIO()
suite = unittest.defaultTestLoader.discover(str(root / 'tests'))
result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
report = dict(tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
              skipped=len(result.skipped), passed=result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
              exit_code=0 if result.wasSuccessful() else 1, elapsed_seconds=time.monotonic()-started,
              python310_syntax_files=len(parsed), bash_syntax_files=2,
              hardware_execution='NOT_RUN', model_inference='NOT_RUN')
save_text(root / 'reports/TEST_RESULTS.txt', stream.getvalue())
save_json(root / 'reports/TEST_RESULTS.json', report)
print(json.dumps(report, ensure_ascii=False))
raise SystemExit(report['exit_code'])

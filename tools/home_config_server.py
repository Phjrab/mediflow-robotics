"""공통 설정의 화면용 부분만 제공한다. 장치를 열지 않는다."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import time
from pathlib import Path
from config_runtime import load,criteria


def thermal_snapshot(path=None, now=None):
    """실행기 캐시만 읽는다. LeLab/관절 사이트가 serial을 따로 열지 않는다."""
    path = path or Path.home()/'.cache/so101-vla-monitor/status.json'
    now = time.time() if now is None else now
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        age = now - float(data['updated'])
        diagnostics = data.get('thermal_diagnostics') or {}
        if not 0 <= age < 1.5 or not diagnostics or data.get('phase') in ('hold', 'stopped', 'run_complete'):
            return {'available': False, 'message': '새 온도 기록 대기', 'filter_controls_motion': False}
        return {'available': True, 'age_s': age, 'joints': diagnostics,
                'temperature_c': data.get('temperature'), 'voltage_v': data.get('voltage_v'),
                'protection': data.get('temperature_protection'),
                'filter_controls_motion': False}
    except (OSError, ValueError, TypeError, KeyError):
        return {'available': False, 'message': '온도 기록 없음', 'filter_controls_motion': False}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/thermal':
            payload = thermal_snapshot()
        elif self.path in ('/', '/config'):
            c=load();payload={k:c[k] for k in ['joint_names','home','limits','calibration_ranges','temperature']}
        else:
            self.send_error(404);return
        data=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin','*');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
    def log_message(self,*args):pass
if __name__=='__main__':ThreadingHTTPServer(('0.0.0.0',8767),Handler).serve_forever()

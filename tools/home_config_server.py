"""공통 설정의 화면용 부분만 제공한다. 장치를 열지 않는다."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import time
from pathlib import Path
from config_runtime import load,criteria,command_limits
from start_position_rules import assess_history


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
            c=load();payload={k:c[k] for k in ['joint_names','home','limits','calibration_ranges','temperature','position_checks','gripper']}
            payload['limits']=command_limits(c)
            payload['home_criteria']=criteria(c)
        else:
            self.send_error(404);return
        data=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin','*');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
    def do_OPTIONS(self):
        self.send_response(204);self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Content-Type');self.end_headers()
    def do_POST(self):
        if self.path!='/start-check':self.send_error(404);return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=32768:raise ValueError('표본 요청 크기 오류')
            payload=assess_history(json.loads(self.rfile.read(size))['samples'])
        except (ValueError,TypeError,KeyError) as exc:
            self.send_response(400);payload={'error':str(exc)}
        else:self.send_response(200)
        data=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode()
        self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
    def log_message(self,*args):pass
if __name__=='__main__':ThreadingHTTPServer(('0.0.0.0',8767),Handler).serve_forever()

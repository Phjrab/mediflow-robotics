"""공통 설정의 화면용 부분만 제공한다. 장치를 열지 않는다."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from config_runtime import load,criteria

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        c=load();data=json.dumps({k:c[k] for k in ['joint_names','home','limits','calibration_ranges']},ensure_ascii=False).encode()
        self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin','*');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
    def log_message(self,*args):pass
if __name__=='__main__':ThreadingHTTPServer(('0.0.0.0',8767),Handler).serve_forever()

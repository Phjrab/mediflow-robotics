"""무인 읽기 전용 HOME 모니터. 최대 실행 시간 뒤 반드시 포트를 닫는다."""
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import signal
import threading
import time
import nov_readonly as n

PAGE='''<!doctype html><html lang="ko"><meta charset="utf-8"><title>SO-101 HOME 읽기 모니터</title>
<style>body{font:20px sans-serif;background:#111827;color:white;margin:24px}table{border-collapse:collapse}td,th{padding:12px;border:1px solid #64748b}.g{background:#14532d}.y{background:#713f12}.r{background:#7f1d1d}</style>
<h1>HOME 읽기 전용</h1><p>토크ON 또는45°C 이상이면 즉시 읽기를 중단하고 포트를 닫습니다. 자동 이동·토크 변경 없음.</p><p id="status">대기</p><table><thead><tr><th>관절</th><th>현재</th><th>목표</th><th>차이</th><th>토크</th><th>온도</th><th>단위</th></tr></thead><tbody id="rows"></tbody></table>
<script>async function tick(){try{const r=await fetch('/state',{cache:'no-store'}),s=await r.json();rows.replaceChildren();if(s.error){document.getElementById('status').textContent=s.error;return;}document.getElementById('status').textContent='토크 모두OFF · 온도45°C 미만 · '+s.age_seconds.toFixed(2)+'초 전';for(const v of s.rows){let tr=document.createElement('tr');tr.className=Math.abs(v.diff)<=3?'g':Math.abs(v.diff)<=5?'y':'r';for(const x of [v.joint,v.current.toFixed(2),v.target.toFixed(2),v.diff.toFixed(2),v.torque,v.temp,v.unit]){let td=document.createElement('td');td.textContent=x;tr.append(td);}rows.append(tr);}}catch(e){document.getElementById('status').textContent='연결 종료/갱신 오류';rows.replaceChildren();}}setInterval(tick,300);tick();</script></html>'''

class Cache:
    def __init__(self):self.lock=threading.Lock();self.value={'error':'아직 읽지 않음'};self.updated=0
    def put(self,snapshot,home):
        rows=[dict(joint=j,current=snapshot['positions'][j],target=home['state'][i],
                   diff=snapshot['positions'][j]-home['state'][i],torque=snapshot['torque'][j],
                   temp=snapshot['temperatures'][j],unit='퍼센트포인트' if i==5 else '도') for i,j in enumerate(n.JOINTS)]
        with self.lock:self.value={'rows':rows};self.updated=time.monotonic()
    def error(self,message):
        with self.lock:self.value={'error':message};self.updated=time.monotonic()
    def get(self):
        with self.lock:
            age=time.monotonic()-self.updated
            return dict(self.value,age_seconds=age) if age<=1.5 else {'error':'관측 지연; 이전 통과 표시 무효'}

def handler(cache):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path=='/':body=PAGE.encode();mime='text/html; charset=utf-8'
            elif self.path=='/state':body=json.dumps(cache.get(),ensure_ascii=False,allow_nan=False).encode();mime='application/json; charset=utf-8'
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    return Handler

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--host',default='127.0.0.1')
    p.add_argument('--http-port',type=int,default=8765);p.add_argument('--max-seconds',type=float,default=300)
    args=p.parse_args()
    if not 0<args.max_seconds<=300:p.error('최대 실행 시간은0초 초과300초 이하')
    n.check_stop();home=n.read_home();cache=Cache();stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
    server=ThreadingHTTPServer((args.host,args.http_port),handler(cache));server.daemon_threads=True
    worker=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.1},daemon=True);worker.start()
    folder=n.ROOT/'reports';folder.mkdir(exist_ok=True)
    out=folder/('home_monitor_'+str(time.time_ns())+'.jsonl');count=0
    try:
        with out.open('x',encoding='utf-8') as stream,n.ReadSession() as session:
            deadline=time.monotonic()+args.max_seconds
            while not stop.is_set() and time.monotonic()<deadline:
                snapshot=session.read(home);cache.put(snapshot,home)
                stream.write(json.dumps(snapshot,ensure_ascii=False)+'\n');stream.flush();count+=1
                if count==1:print('HOME_MONITOR_READONLY_READY rows=6',flush=True)
                stop.wait(0.25)
    except n.SafetyStop as exc:
        cache.error(str(exc));print('SERIAL_STOP: '+str(exc),flush=True);raise
    finally:
        stop.set();server.shutdown();worker.join(timeout=2);server.server_close()
        print('HOME_MONITOR_CLOSED: 포트 해제, 레지스터 쓰기0; samples='+str(count),flush=True)

if __name__=='__main__':main()

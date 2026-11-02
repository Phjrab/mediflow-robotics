from __future__ import annotations

import json
import threading
from copy import deepcopy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .errors import SorterError
from .fixed_demo import (DEFAULT_DEMO_MOTION_PROFILE, DemoSettings, MOTION_PROFILES,
                         VALID_BOTTLE_TYPES, VALID_DESTINATIONS, run_demo)


INDEX_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SO-101 약통 분류 데모</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#10151c;color:#eef4fb;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}main{max-width:1100px;margin:auto;padding:22px}h1{margin:0 0 6px}.note{color:#b7c7d9}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:14px}.card{background:#18212b;border:1px solid #2c3b4b;border-radius:12px;padding:15px}.choices{display:flex;flex-wrap:wrap;gap:7px}.choices input{position:absolute;opacity:0}.choices label,.preset,button{border:1px solid #496176;background:#243343;color:#eef4fb;border-radius:9px;padding:10px 12px;cursor:pointer;font-weight:700}.choices input:checked+label{background:#1770bf;border-color:#5eb1ff}.actions{display:flex;gap:10px;flex-wrap:wrap;margin:15px 0}.primary{background:#1675c1}.stop{background:#b5202a;border-color:#ff6670;font-size:18px}.stop:disabled,button:disabled{opacity:.38;cursor:not-allowed}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0b1117;border-radius:9px;padding:12px;min-height:64px}.stage{font-size:22px;font-weight:800;color:#8fd3ff}.ok{color:#82e6a7}.warn{color:#ffc66d}img{width:100%;min-height:220px;object-fit:contain;background:#05080b;border-radius:9px;border:1px solid #2c3b4b}
</style></head><body><main>
<h1>SO-101 약통 분류 데모</h1><p class="note">A=칸1 · B=칸2 · C=칸3 검증 배치 전용입니다. 실행 전 세 마커와 모터 온도 &lt;50°C를 자동 확인합니다.</p><p class="ok">현재 motion profile: <strong id="profile">확인 중</strong></p>
<section class="grid"><div class="card"><h2>분류 규칙</h2>
<div id="rules"></div><h3>프리셋</h3><div class="choices">
<button class="preset" data-rules="A=blue,B=red,C=green">A→파랑 B→빨강 C→초록</button>
<button class="preset" data-rules="A=green,B=blue,C=red">A→초록 B→파랑 C→빨강</button>
<button class="preset" data-rules="A=red,B=green,C=blue">A→빨강 B→초록 C→파랑</button>
</div></div><div class="card"><h2>Astra 8020</h2><img id="camera" alt="8020 Astra 카메라"><p><a id="camera-link" target="_blank" rel="noopener">카메라 화면 새 창에서 열기</a></p></div></section>
<div class="actions"><button id="plan" class="primary">계획 확인</button><button id="run" class="primary">실행</button><button id="stop" class="stop" disabled>정지 — 현재 위치 HOLD</button></div>
<section class="grid"><div class="card"><h2>현재 단계</h2><div id="stage" class="stage">대기 중</div><h3>모터 온도</h3><pre id="temps">아직 읽지 않음</pre><p id="cooldown" class="warn">실행 완료 후 “전원을 뽑고 식히세요” 안내가 표시됩니다.</p></div><div class="card"><h2>계획</h2><pre id="plan-result">계획 확인을 누르세요.</pre></div></section>
<section class="card"><h2>마지막 오류 / 정지 결과</h2><pre id="error">없음</pre></section>
<script>
const labels={A:'A 큰 약통',B:'B 중간 약통',C:'C 작은 약통'},colors=[['red','빨강'],['blue','파랑'],['green','초록'],['skip','건너뛰기']];
const rules=document.querySelector('#rules');for(const type of ['A','B','C']){const box=document.createElement('div');box.innerHTML='<h3>'+labels[type]+'</h3><div class="choices">'+colors.map(([v,t],i)=>'<input type="radio" name="'+type+'" id="'+type+'-'+v+'" value="'+v+'" '+(i===3?'checked':'')+'><label for="'+type+'-'+v+'">'+t+'</label>').join('')+'</div>';rules.appendChild(box)}
function selected(){const out={};for(const t of ['A','B','C'])out[t]=document.querySelector('input[name="'+t+'"]:checked').value;return out}
function setRules(text){for(const pair of text.split(',')){const [t,v]=pair.split('=');document.querySelector('#'+t+'-'+v).checked=true}}
for(const b of document.querySelectorAll('.preset'))b.onclick=()=>setRules(b.dataset.rules);
async function post(path){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rules:selected()})}),d=await r.json();if(!r.ok)throw d;return d}
document.querySelector('#plan').onclick=async()=>{try{const d=await post('/api/plan');document.querySelector('#plan-result').textContent=JSON.stringify(d,null,2)}catch(e){document.querySelector('#error').textContent=JSON.stringify(e,null,2)}};
document.querySelector('#run').onclick=async()=>{try{const d=await post('/api/run');document.querySelector('#plan-result').textContent=JSON.stringify(d,null,2)}catch(e){document.querySelector('#error').textContent=JSON.stringify(e,null,2)}};
document.querySelector('#stop').onclick=async()=>{try{const r=await fetch('/api/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});document.querySelector('#error').textContent=JSON.stringify(await r.json(),null,2)}catch(e){document.querySelector('#error').textContent=String(e)}};
async function poll(){try{const r=await fetch('/api/status',{cache:'no-store'}),d=await r.json(),busy=d.running===true;for(const id of ['plan','run'])document.querySelector('#'+id).disabled=busy;for(const x of document.querySelectorAll('input,.preset'))x.disabled=busy;document.querySelector('#stop').disabled=!busy;document.querySelector('#profile').textContent=d.motion_profile||'알 수 없음';document.querySelector('#stage').textContent=d.stage?(d.stage_index+'/'+d.stage_count+' '+d.stage):d.status;document.querySelector('#temps').textContent=d.temperatures_c?JSON.stringify(d.temperatures_c,null,2):'아직 읽지 않음';document.querySelector('#error').textContent=d.last_error?JSON.stringify(d.last_error,null,2):'없음';document.querySelector('#cooldown').textContent=d.cooldown_notice||'';if(d.last_plan)document.querySelector('#plan-result').textContent=JSON.stringify(d.last_plan,null,2)}catch(e){document.querySelector('#error').textContent=String(e)}}
const camera='http://'+location.hostname+':8020/stream.mjpg';document.querySelector('#camera').src=camera;document.querySelector('#camera-link').href='http://'+location.hostname+':8020/';poll();setInterval(poll,500);
</script></main></body></html>"""


def parse_web_rules(payload: Any) -> dict[str, str]:
    if not isinstance(payload, Mapping):
        raise SorterError("web_rules_invalid", type(payload).__name__, "rules object",
                          "A/B/C 목적지를 다시 선택해 주세요")
    unknown = set(payload) - VALID_BOTTLE_TYPES
    if unknown:
        raise SorterError("web_rule_bottle_invalid", sorted(unknown), "A, B, C",
                          "알 수 없는 약통 규칙을 제거해 주세요")
    rules = {}
    for label in sorted(VALID_BOTTLE_TYPES):
        value = payload.get(label, "skip")
        if value == "skip":
            continue
        if value not in VALID_DESTINATIONS:
            raise SorterError("web_rule_destination_invalid", {label: value},
                              "red, blue, green, or skip",
                              "목적지를 다시 선택해 주세요")
        rules[label] = value
    return rules


class DemoWebController:
    def __init__(self, config, settings: DemoSettings, *,
                 motion_profile: str = DEFAULT_DEMO_MOTION_PROFILE,
                 run_function: Callable[..., dict[str, Any]] = run_demo):
        if motion_profile not in MOTION_PROFILES:
            raise ValueError(f"motion_profile_invalid:{motion_profile}")
        self.config = config
        self.settings = settings
        self.motion_profile = motion_profile
        self.run_function = run_function
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.state: dict[str, Any] = {
            "status": "IDLE", "running": False, "stage": None,
            "stage_index": 0, "stage_count": 0, "temperatures_c": None,
            "last_error": None, "last_plan": None, "last_result": None,
            "cooldown_notice": None, "motion_profile": motion_profile,
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return deepcopy(self.state)

    def _progress(self, payload: Mapping[str, Any]) -> None:
        with self.lock:
            self.state.update({key: value for key, value in payload.items()
                               if key in {"status", "stage", "stage_index", "stage_count",
                                          "temperatures_c"}})

    def _remember_error(self, error: SorterError) -> None:
        with self.lock:
            self.state["last_error"] = error.as_dict()

    def plan(self, payload: Any) -> dict[str, Any]:
        try:
            rules = parse_web_rules(payload)
        except SorterError as error:
            self._remember_error(error)
            raise
        with self.lock:
            if self.state["running"]:
                raise SorterError("web_demo_busy", self.state["stage"], "idle",
                                  "현재 실행이 끝난 뒤 계획을 확인해 주세요")
        try:
            result = self.run_function(
                self.config, self.settings, dry_run=True, step=False, auto=False,
                motion_profile=self.motion_profile, rules=rules)
        except SorterError as error:
            self._remember_error(error)
            raise
        with self.lock:
            self.state["last_plan"] = result
            self.state["last_error"] = None
        return result

    def start(self, payload: Any) -> dict[str, Any]:
        try:
            rules = parse_web_rules(payload)
        except SorterError as error:
            self._remember_error(error)
            raise
        with self.lock:
            if self.state["running"]:
                raise SorterError("web_demo_busy", self.state["stage"], "idle",
                                  "현재 실행을 먼저 완료하거나 정지해 주세요")
            self.stop_event.clear()
            self.state.update({"status": "STARTING", "running": True, "stage": None,
                               "stage_index": 0, "stage_count": 0,
                               "temperatures_c": None, "last_error": None,
                               "last_result": None, "cooldown_notice": None})
            self.thread = threading.Thread(target=self._worker, args=(rules,),
                                           name="fixed-demo-auto-run", daemon=True)
            self.thread.start()
        return {"status": "STARTED", "rules": rules,
                "motion_profile": self.motion_profile}

    def _worker(self, rules: Mapping[str, str]) -> None:
        try:
            result = self.run_function(
                self.config, self.settings, dry_run=False, step=False, auto=True,
                motion_profile=self.motion_profile, rules=rules,
                progress_callback=self._progress,
                stop_requested=self.stop_event.is_set, install_signal_handlers=False)
        except SorterError as error:
            with self.lock:
                self.state.update({
                    "status": "STOPPED" if self.stop_event.is_set() else "ERROR",
                    "running": False, "last_error": error.as_dict(),
                    "cooldown_notice": "전원을 뽑고 식히세요",
                })
        except BaseException as error:
            with self.lock:
                self.state.update({
                    "status": "ERROR", "running": False,
                    "last_error": {"status": "FAILED", "error": type(error).__name__,
                                   "observed": str(error)},
                    "cooldown_notice": "전원을 뽑고 식히세요",
                })
        else:
            with self.lock:
                self.state.update({"status": "COMPLETE", "running": False,
                                   "last_result": result,
                                   "temperatures_c": result.get("final_temperatures_c"),
                                   "cooldown_notice": "전원을 뽑고 식히세요"})

    def stop(self) -> dict[str, Any]:
        with self.lock:
            if not self.state["running"]:
                return {"status": "NOT_RUNNING"}
            self.stop_event.set()
            self.state["status"] = "STOP_REQUESTED"
            return {"status": "STOP_REQUESTED", "stage": self.state["stage"]}

    def wait(self, timeout: float | None = None) -> bool:
        thread = self.thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()


def make_handler(controller: DemoWebController):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MedicineSorterDemo/1.0"

        def log_message(self, fmt, *args):
            print(f"web {self.address_string()} {fmt % args}")

        def _json(self, payload: Mapping[str, Any], status: int = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/":
                body = INDEX_HTML.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/status":
                self._json(controller.snapshot())
            else:
                self._json({"status": "FAILED", "error": "not_found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            path = urlsplit(self.path).path
            content_type = self.headers.get("Content-Type", "")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if not content_type.startswith("application/json") or not 0 <= length <= 32768:
                self._json({"status": "FAILED", "error": "json_request_required"},
                           HTTPStatus.BAD_REQUEST)
                return
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                if path == "/api/plan":
                    result = controller.plan(payload.get("rules"))
                    self._json(result)
                elif path == "/api/run":
                    self._json(controller.start(payload.get("rules")), HTTPStatus.ACCEPTED)
                elif path == "/api/stop":
                    self._json(controller.stop())
                else:
                    self._json({"status": "FAILED", "error": "not_found"},
                               HTTPStatus.NOT_FOUND)
            except (json.JSONDecodeError, AttributeError):
                self._json({"status": "FAILED", "error": "invalid_json"},
                           HTTPStatus.BAD_REQUEST)
            except SorterError as error:
                self._json(error.as_dict(), HTTPStatus.CONFLICT)

    return Handler


def serve_demo_web(config, settings: DemoSettings, *, port: int,
                   motion_profile: str = DEFAULT_DEMO_MOTION_PROFILE,
                   host: str = "0.0.0.0",
                   controller: DemoWebController | None = None,
                   server_factory=ThreadingHTTPServer) -> None:
    active_controller = controller or DemoWebController(
        config, settings, motion_profile=motion_profile)
    server = server_factory((host, port), make_handler(active_controller))
    server.daemon_threads = True
    print(f"SO-101 fixed demo web ({active_controller.motion_profile}): http://{host}:{port}/")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        active_controller.stop()
        active_controller.wait()
    finally:
        server.server_close()

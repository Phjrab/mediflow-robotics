"""서비스 정지 전에 텔레옵/녹화를 정상 종료한다."""
import json
import time
from urllib.request import Request,urlopen
from urllib.error import URLError

def request(path,method='GET'):
    with urlopen(Request('http://127.0.0.1:8022/'+path,method=method),timeout=10) as r:return json.load(r)

def main():
    for name in ['teleoperation','recording','inference']:
        try:r=request(name+'-status')
        except URLError:return
        if r.get(name+'_active') is True:
            request('stop-'+name,'POST');deadline=time.monotonic()+25
            while request(name+'-status').get(name+'_active') is not False:
                if time.monotonic()>deadline:raise RuntimeError('LeLab 작업 종료 미확인: '+name)
                time.sleep(.25)
if __name__=='__main__':main()

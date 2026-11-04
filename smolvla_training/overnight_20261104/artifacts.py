import csv
import json
import os
from pathlib import Path
import shutil
import time

def backup(path):
    path=Path(path)
    if path.exists():
        dest=path.with_name(path.name+'.bak_'+time.strftime('%Y%m%d_%H%M%S')+'_'+str(time.time_ns()%1000000))
        shutil.copy2(path,dest);return dest
def save_text(path,text):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);backup(path)
    temp=path.with_name(path.name+'.tmp_'+str(time.time_ns()))
    with temp.open('x',encoding='utf-8',newline='\n') as out:out.write(text);out.flush();os.fsync(out.fileno())
    os.replace(temp,path)
def save_json(path,value):save_text(path,json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def save_csv(path,rows):
    import io
    stream=io.StringIO();rows=list(rows)
    if rows:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    save_text(path,stream.getvalue())
def temperatures():
    result={}
    for path in Path('/sys/class/thermal').glob('thermal_zone*'):
        try:result[(path/'type').read_text().strip()]=float((path/'temp').read_text())/1000
        except (OSError,ValueError):continue
    return result or 'UNVERIFIED'

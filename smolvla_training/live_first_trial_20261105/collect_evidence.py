"""이번 실행 결과만 별도 ZIP으로 수집. 원본/모델/보정 파일은 수정하지 않는다."""
import argparse
import json
from pathlib import Path
import re
import socket
import zipfile

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--out",type=Path,required=True);parser.add_argument("roots",nargs="+",type=Path);args=parser.parse_args()
    def masked(text):
        text=text.replace(str(Path.home()),"~").replace(Path.home().name,"<MASK>")
        text=text.replace(socket.gethostname(),"<MASK>")
        return re.sub(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])","<MASK>",text)
    entries=[]
    with zipfile.ZipFile(args.out,"x",compression=zipfile.ZIP_DEFLATED) as z:
        for source in args.roots:
            source=source.expanduser().resolve()
            for path in sorted(source.rglob("*")):
                if path.is_file() and path.suffix in (".json",".jsonl",".csv",".log",".txt"):
                    name=str(Path(source.name)/path.relative_to(source)).replace("\\","/")
                    z.writestr(name,masked(path.read_text(encoding="utf-8",errors="replace")));entries.append(name)
        z.writestr("COLLECTION.json",json.dumps(dict(files=entries,weights_included=False,originals_changed=False),ensure_ascii=False,indent=2))
    print(json.dumps(dict(status="EVIDENCE_SAVED",files=len(entries),bytes=args.out.stat().st_size),ensure_ascii=False))

if __name__=="__main__":main()

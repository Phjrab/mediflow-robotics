from pathlib import Path
import sys
def prepare():
 p=Path(__file__).resolve().parent
 if str(p) not in sys.path:sys.path.insert(0,str(p))
 return p

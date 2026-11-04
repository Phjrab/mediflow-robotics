from pathlib import Path
import re
import subprocess
import unittest

class Tests(unittest.TestCase):
    def test_occupancy_function_refuses_pid_and_unknown(self):
        bash=Path('C:/Program Files/Git/bin/bash.exe')
        if not bash.exists():bash=Path('/bin/bash')
        source=Path(__file__).parents[1].joinpath('nov_preflight.sh').read_text(encoding='utf-8')
        function=re.search(r'require_no_owners\(\) \{[\s\S]+?^\}',source,re.M).group(0)
        for status,text,expected in [(0,'monitor_pid',1),(1,'Permission denied',1),(1,'',0),(127,'',1)]:
            result=subprocess.run([str(bash),'--noprofile','--norc','-c',function+'\nrequire_no_owners "$1" "$2"','mock',str(status),text],capture_output=True)
            self.assertEqual(result.returncode,expected)
    def test_dry_wrapper_refuses_other_modes_before_python(self):
        bash=Path('C:/Program Files/Git/bin/bash.exe')
        if not bash.exists():bash=Path('/bin/bash')
        script=Path(__file__).parents[1]/'nov_policy_run.sh'
        for mode in ('live','--live','ENABLE','unknown'):
            result=subprocess.run([str(bash),str(script),mode],capture_output=True)
            self.assertEqual(result.returncode,2)

if __name__=='__main__':unittest.main()

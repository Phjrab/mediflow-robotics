from config_runtime import load,criteria
HOME_TOLERANCE=load()['home']['tolerance_deg']
def start_criteria(limits):return criteria()
def validate_position(present_raw,limits):
 for j in limits:
  v=present_raw.get(j)
  if type(v) is not int or not 0<=v<=4095:raise ValueError('위치 형식 오류: '+j)

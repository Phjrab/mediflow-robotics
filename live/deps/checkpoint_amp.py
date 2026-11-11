"""저장된 AMP 여부만 적용. 가중치 변환/정밀도 자동 선택 기능 없음."""
def saved_amp(config):
    value=config.get("use_amp")
    if type(value) is not bool:raise ValueError("체크포인트 use_amp 값 미확인")
    return value

def infer_with_saved_amp(predict,torch,config):
    with torch.autocast(device_type="cuda",enabled=saved_amp(config)):
        return predict()

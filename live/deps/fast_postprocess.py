"""저장 후처리를 chunk 전체에 한 번 적용. 직접 정규화 계산/단위 변환 없음."""
import math

def validate_post_config(config):
    steps=config.get("steps",[])
    if [s.get("registry_name") for s in steps]!=["unnormalizer_processor","device_processor"]:
        raise ValueError("일괄 처리 검증 범위 밖인 후처리 구성")
    if steps[0].get("config",{}).get("features",{}).get("action",{}).get("shape")!=[6]:
        raise ValueError("action6 형식 아님")
    if steps[0].get("config",{}).get("norm_map",{}).get("ACTION")!="MEAN_STD" or steps[1].get("config",{}).get("device")!="cpu":
        raise ValueError("확인된 저장 후처리 설정과 다름")

def rows_from_tensor(tensor):
    if tuple(tensor.shape)!=(1,50,6):raise ValueError("후처리 chunk shape 오류")
    rows=tensor.detach().float().cpu().reshape(50,6).tolist()
    if len(rows)!=50 or any(len(r)!=6 or any(type(x) not in (float,int) or not math.isfinite(x) for x in r) for r in rows):
        raise ValueError("후처리 값 NaN/Inf/차원 오류")
    return rows

def bulk_actions(postprocess,normalized):
    if tuple(normalized.shape)!=(1,50,6):raise ValueError("정책 chunk shape 오류")
    return rows_from_tensor(postprocess(normalized))

def infer_chunk_fast(policy,preprocess,postprocess,observation,torch):
    batch=preprocess(dict(observation))
    with torch.inference_mode():
        normalized=policy.predict_action_chunk(batch)
        if tuple(normalized.shape)!=(1,50,6) or not bool(torch.isfinite(normalized).all()):
            raise ValueError("정책 chunk shape/NaN/Inf 오류")
        return bulk_actions(postprocess,normalized)

"""Recompute image-plane pan displacement from stored probe images; no devices."""
import argparse
import json
from pathlib import Path


def main():
    import cv2
    import numpy as np
    p=argparse.ArgumentParser();p.add_argument('--probe',type=Path,required=True);p.add_argument('--marker-side-mm',type=float,required=True)
    args=p.parse_args()
    if not 0<args.marker_side_mm<1000:p.error('physical marker side in mm required')
    d=json.loads((args.probe/'summary.json').read_text());detector=cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50))
    phases={};origin=right=None;aim=np.array([320.,240.],np.float32)
    def plane(h,point):return cv2.perspectiveTransform(np.array([[point]],np.float32),h)[0,0]
    for phase in d['phases']:
        end=phase['samples'][-1]['unix'];samples=[s for s in phase['samples'] if end-s['unix']<=.8];measurements=[]
        for image in phase['images']:
            if end-image['captured']>1:continue
            file=args.probe/image['files']['end_effector'];pixels=cv2.imread(str(file))
            if pixels is None or pixels.shape[:2]!=(480,640):raise ValueError('expected640x480 stored wrist image')
            corners,ids,_=detector.detectMarkers(cv2.cvtColor(pixels,cv2.COLOR_BGR2GRAY))
            markers={} if ids is None else {int(mid):c.reshape(4,2) for mid,c in zip(ids.flatten(),corners)}
            if 9 not in markers:continue
            c=markers[9];h=cv2.getPerspectiveTransform(c,np.array([[0,0],[1,0],[1,1],[0,1]],np.float32))
            if origin is None:
                origin=plane(h,aim);right=plane(h,aim+[10,0])-origin;right/=np.linalg.norm(right)
            measurements.append(dict(file=file.name,marker_center_px=c.mean(0).tolist(),aim_right_marker_units=float(np.dot(plane(h,aim)-origin,right))))
        if len(measurements)<2:raise ValueError('need at least2 independent marker frames perphase')
        phases[phase['phase']]=dict(pan_actual_mean=float(np.mean([s['present_raw']['shoulder_pan'] for s in samples])),
            target_raw=phase['target_raw'],marker_center_px=np.mean([m['marker_center_px'] for m in measurements],0).tolist(),
            aim_right_marker_units=float(np.mean([m['aim_right_marker_units'] for m in measurements])),measurements=measurements)
    a,b=phases['baseline'],phases['plus6'];result=dict(marker_id=9,marker_side_mm=args.marker_side_mm,aim_pixel=aim.tolist(),
        actual_pan_delta_ticks=b['pan_actual_mean']-a['pan_actual_mean'],command_delta_ticks=b['target_raw']-a['target_raw'],
        marker_shift_px=(np.array(b['marker_center_px'])-a['marker_center_px']).tolist(),
        screen_right_mm=(b['aim_right_marker_units']-a['aim_right_marker_units'])*args.marker_side_mm,
        return_right_mm=(phases['return']['aim_right_marker_units']-a['aim_right_marker_units'])*args.marker_side_mm,
        phases=phases,scope='camera aim projection onto bottle marker plane, not full3D TCP')
    (args.probe/'camera_direction_30mm.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='phases'}))


if __name__=='__main__':main()

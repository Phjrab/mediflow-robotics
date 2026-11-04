import json
from pathlib import Path
import struct
import tempfile
import unittest
import policy_api as api

class Tests(unittest.TestCase):
    def test_actual_header_parse_validates_six_joint_statistics(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            features={'observation.state':{'shape':[6]}}
            features.update({'observation.images.camera'+str(i):{'shape':[3,256,256]} for i in (1,2,3)})
            (root/'config.json').write_text(json.dumps({'type':'smolvla','input_features':features,'output_features':{'action':{'shape':[6]}}}))
            mapping={'observation.images.'+camera:'observation.images.camera'+str(i) for i,camera in enumerate(api.CAMERAS,1)}
            header=json.dumps({'action.mean':{'shape':[6],'dtype':'F32','data_offsets':[0,24]}}).encode()
            for name in ('pre.safetensors','post.safetensors'):
                (root/name).write_bytes(struct.pack('<Q',len(header))+header+struct.pack('<6f',1,2,3,4,5,6))
            (root/'policy_preprocessor.json').write_text(json.dumps({'steps':[{'registry_name':'rename_observations_processor','config':{'rename_map':mapping}},{'state_file':'pre.safetensors'}]}))
            (root/'policy_postprocessor.json').write_text(json.dumps({'steps':[{'state_file':'post.safetensors'}]}))
            (root/'train_config.json').write_text(json.dumps({'steps':20000}))
            data=api.inspect_checkpoint(root)
            self.assertEqual(data['checkpoint_kind'],'SO101_FINETUNED_CANDIDATE')
            self.assertEqual(data['normalization_summary']['pre.safetensors:action.mean'],[1.,2.,3.,4.,5.,6.])
    def test_pure_module_has_no_device_imports(self):
        text=Path(api.__file__).read_text(encoding='utf-8')
        self.assertNotIn('SO101Follower',text);self.assertNotIn('serial.Serial',text)
        self.assertNotIn('send_action(',text);self.assertNotIn('sync_write(',text)

if __name__=='__main__':unittest.main()

import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import software_preflight as software
import observation_contract as contract
import policy_api as api


def checkpoint(root):
    root.mkdir()
    inputs={'observation.state':{'shape':[6]},**{'observation.images.camera'+str(i):{'shape':[3,256,256]} for i in (1,2,3)}}
    (root/'config.json').write_text(json.dumps({'type':'smolvla','input_features':inputs,'output_features':{'action':{'shape':[6]}}}))
    header=json.dumps({'action.mean':{'shape':[6],'dtype':'F32','data_offsets':[0,24]}}).encode()
    for name in ('pre.safetensors','post.safetensors'):(root/name).write_bytes(struct.pack('<Q',len(header))+header+struct.pack('<6f',1,2,3,4,5,6))
    mapping={'observation.images.'+c:'observation.images.camera'+str(i) for i,c in enumerate(api.CAMERAS,1)}
    (root/'policy_preprocessor.json').write_text(json.dumps({'steps':[{'registry_name':'rename_observations_processor','config':{'rename_map':mapping}},{'state_file':'pre.safetensors'}]}))
    (root/'policy_postprocessor.json').write_text(json.dumps({'steps':[{'state_file':'post.safetensors'}]}))
    (root/'train_config.json').write_text(json.dumps({'steps':20000}))
    return root


class Tests(unittest.TestCase):
    def test_metadata_pass_never_means_hardware_ready_and_stop_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ckpt=checkpoint(root/'ckpt');stop=root/'SERIAL_STOP.json';stop.write_text('latched')
            with patch.object(software,'ROOT',root),patch('builtins.__import__',wraps=__import__) as imports:
                result=software.inspect_software(ckpt,contract.CONFIG)
            self.assertEqual(result['status'],'SOFTWARE_CONFIG_PASS');self.assertFalse(result['hardware_ready'])
            self.assertTrue(result['serial_stop_present']);self.assertEqual(stop.read_text(),'latched')
            self.assertFalse(any(c.args[0].startswith(('torch','lerobot','serial')) for c in imports.call_args_list))

    def test_missing_statistics_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            ckpt=checkpoint(Path(tmp)/'ckpt');(ckpt/'post.safetensors').unlink()
            with self.assertRaises(ValueError):software.inspect_software(ckpt,contract.CONFIG)


if __name__=='__main__':unittest.main()

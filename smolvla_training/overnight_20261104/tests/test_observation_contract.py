import copy
import json
from pathlib import Path
import tempfile
import types
import unittest
import observation_contract as contract
import policy_api as api


class Tensor:
    def permute(self, *args): return self
    def unsqueeze(self, *args): return self
    def float(self): return self
    def __truediv__(self, value):
        if value != 255.0: raise AssertionError('uint8 정규화 불일치')
        return self


class Frame:
    shape = (480, 640, 3)
    dtype = 'uint8'
    def copy(self): return self


class Tests(unittest.TestCase):
    def write_config(self, value, path):
        path.write_text(json.dumps(value), encoding='utf-8')
        return contract.load_contract(path)

    def test_unknown_camera_serial_path_and_short_warmup_refused(self):
        original = contract.load_contract()
        with tempfile.TemporaryDirectory() as tmp:
            for field, value in [('warmup_seconds', 1), ('warmup_seconds', float('nan')), ('fps', True), ('color_mode', 'bgr')]:
                data = copy.deepcopy(original); data[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.write_config(data, Path(tmp) / 'test.json')
            data = copy.deepcopy(original); data['cameras']['end_effector'] = '/dev/serial/by-id/follower'
            with self.assertRaises(ValueError): self.write_config(data, Path(tmp) / 'test.json')

    def test_mapping_comes_from_checkpoint_not_runtime_camera_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); sources = ['observation.images.' + k for k in api.CAMERAS]
            mapping = dict(zip(sources, ['observation.images.camera1', 'observation.images.camera2', 'observation.images.camera3']))
            cfg = {'input_features': {'observation.state': {'shape': [6]}, **{k: {'shape': [3, 256, 256]} for k in mapping.values()}}}
            pre = {'steps': [{'registry_name': 'rename_observations_processor', 'config': {'rename_map': mapping}}]}
            (root / 'config.json').write_text(json.dumps(cfg)); (root / 'policy_preprocessor.json').write_text(json.dumps(pre))
            self.assertEqual(contract.saved_mapping(root), mapping)
            pre['steps'][0]['config']['rename_map'] = {v: k for k, v in mapping.items()}
            (root / 'policy_preprocessor.json').write_text(json.dumps(pre))
            with self.assertRaises(ValueError): contract.saved_mapping(root)

    def test_pack_preserves_dataset_keys_and_state_units(self):
        value = contract.load_contract(); frames = {k: Frame() for k in api.CAMERAS}; captured = []
        fake = types.SimpleNamespace(float32='float32', tensor=lambda state, **kw: captured.append(state) or Tensor(), from_numpy=lambda frame: Tensor())
        state = [-2., -97., 96., 43., -4., 7.]
        packed = contract.pack_observation(frames, state, api.COLOR_TASKS['blue'], fake, value)
        self.assertEqual(captured, [[state]])
        self.assertEqual(set(packed), {'task', 'observation.state'} | {'observation.images.' + k for k in api.CAMERAS})
        self.assertFalse(any(k.startswith('observation.images.camera') for k in packed))

    def test_wrong_frame_type_double_rename_and_unknown_task_refused(self):
        value = contract.load_contract(); frames = {k: Frame() for k in api.CAMERAS}
        frames['end_effector'] = types.SimpleNamespace(shape=(480, 640, 3), dtype='float32')
        with self.assertRaises(ValueError): contract.validate_frames(frames, value)
        with self.assertRaises(ValueError): contract.validate_frames({'camera1': Frame()}, value)
        with self.assertRaises(ValueError): contract.pack_observation({k: Frame() for k in api.CAMERAS}, [0.] * 6, '', None, value)


if __name__ == '__main__':
    unittest.main()

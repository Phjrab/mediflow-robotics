"""External metadata attribution is NOT permission to bypass local runtime gates."""
import json
import sys
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from vla_train import load_config,get_rename_map


class MetadataEvidenceTests(unittest.TestCase):
    def test_attribution_keys_and_runtime_separation(self):
        evidence=json.loads((ROOT/'configs/vla_model_metadata_evidence.json').read_text())
        config=load_config(ROOT/'configs/vla_train.yaml')
        self.assertEqual(evidence['evidence_status'],'ATTRIBUTED_EXTERNAL_OBSERVATION')
        self.assertFalse(evidence['local_verification']['raw_digest_independently_verified'])
        self.assertFalse(evidence['local_verification']['raw_config_saved'])
        self.assertEqual(len(evidence['attribution']['reported_raw_sha256']),64)
        self.assertIn('/'+evidence['revision']+'/',evidence['pinned_url'])
        get_rename_map(config,model_keys=evidence['reported_fields']['input_feature_keys'])
        self.assertEqual(evidence['reported_fields']['output_features']['action']['shape'],[6])
        self.assertIsNone(config['model_config'])
        self.assertEqual(config['physical_mapping_status'],'UNVERIFIED_PROJECT_CHOICE')
        self.assertIsNone(config['physical_mapping_evidence'])
        self.assertIs(config['push_to_hub'],False)


if __name__=='__main__': unittest.main()

import unittest
from unittest.mock import MagicMock, Mock

from mediflow_vlm.safe_infer import predict_label


class SafeInferTests(unittest.TestCase):
    def test_predict_label_uses_requested_prompt(self) -> None:
        model = Mock()
        model.device = "cpu"
        model.generate.return_value = MagicMock()
        processor = Mock()
        processor.apply_chat_template.return_value = "rendered"
        input_ids = Mock()
        input_ids.shape = [1, 2]
        input_ids.to.return_value = input_ids
        processor.return_value = {"input_ids": input_ids}
        processor.decode.return_value = "body"

        result = predict_label(model, processor, Mock(), "grasp prompt", 8)

        messages = processor.apply_chat_template.call_args.args[0]
        self.assertEqual(messages[0]["content"][1]["text"], "grasp prompt")
        self.assertEqual(result, "body")


if __name__ == "__main__":
    unittest.main()

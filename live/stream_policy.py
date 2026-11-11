"""Run installed SmolVLA RTC in the existing hardware-isolated transport."""
import time

from process_policy import ProcessPolicy, RealEngine, Transport


class StreamingEngine(RealEngine):
    def __init__(self, options):
        super().__init__(options)
        self.previous = None
        self.origin = None
        try:
            from lerobot.policies.rtc.configuration_rtc import RTCConfig
        except ImportError:
            self.config['stream_rtc'] = False
        else:
            self.config['stream_rtc'] = bool(options.get('rtc', False))
            if self.config['stream_rtc']:
                self.policy.config.rtc_config = RTCConfig(execution_horizon=10)
                self.policy.init_rtc_processor()

    def call(self, command, payload):
        if command == 'reset':
            self.previous = self.origin = None
        if command != 'predict' or not self.config['stream_rtc']:
            return super().call(command, payload)
        import numpy as np
        from checkpoint_amp import infer_with_saved_amp
        from fast_postprocess import bulk_actions

        origin, delay = payload['stream_request']
        torch = self.torch
        observation = {'task': payload['task']}
        for key, row in payload['tensors'].items():
            shape = tuple(row['shape'])
            expected = (1, 6) if key == 'observation.state' else (1, 3, 480, 640)
            if shape != expected or len(row['bytes']) != 4 * int(np.prod(shape)):
                raise ValueError('RTC 관측 형식 불일치')
            observation[key] = torch.from_numpy(np.frombuffer(row['bytes'], dtype=np.float32).copy().reshape(shape))
        prefix = None
        if self.previous is not None and 0 <= origin-self.origin < 50:
            prefix = self.previous[:, origin-self.origin:]
        options = {} if prefix is None else {
            'prev_chunk_left_over': prefix,
            'inference_delay': min(delay, prefix.shape[1]),
            'execution_horizon': min(delay + 10, prefix.shape[1]),
        }
        batch = self.pre(observation)
        torch.cuda.synchronize()
        # RTC's guidance uses enable_grad internally, so it must retain normal tensors.
        action = infer_with_saved_amp(lambda: self.policy.predict_action_chunk(batch, **options), torch, self.config)
        self.previous, self.origin = action.detach().clone(), origin
        result = bulk_actions(self.post, action)
        torch.cuda.synchronize()
        return result


def create_engine(options):
    return StreamingEngine(options)


class StreamingTransport(Transport):
    request = None

    def call(self, command, payload=None):
        if command == 'predict':
            payload['stream_request'] = self.request
        return super().call(command, payload)


class StreamingPolicy(ProcessPolicy):
    def __init__(self, context, task, *, rtc=False):
        from observation_contract import pack_observation
        import torch

        self.pack = pack_observation
        self.torch = torch
        self.context = context
        self.task = task
        self.transport = StreamingTransport(create_engine, {'checkpoint': str(context['checkpoint']), 'rtc': rtc})

    def __call__(self, frames, state, origin, delay):
        started = time.monotonic()
        packed = self.pack({key: frame['pixels'] for key, frame in frames.items()}, state,
                           self.task, self.torch, self.context['contract'])
        self.transport.request = (origin, delay)
        return self.predict_raw(packed), time.monotonic() - started

    def close(self):
        self.transport.close()

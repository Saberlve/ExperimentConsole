"""No robot imports or action execution paths exist in this module."""
import math
import time


class VLAAdapter:
    def prepare(self, payload):
        if not payload.get('task_id') or not payload.get('camera_id'):
            raise ValueError('准备测试需要 task_id 和 camera_id')
        return {'status': 'prepared', 'inputs': payload, 'inference_available': False,
                'detail': '已发现 starVLA 服务，但机器人 eval 会连接并运动；未调用。需补齐 checkpoint/state/视角契约后接入纯推理客户端。'}


class IDMAdapter:
    """Replace infer with a checkpoint-specific implementation after defining its contract."""
    def infer(self, payload):
        started = time.perf_counter()
        if payload.get('mode') != 'mock':
            raise ValueError('未配置真实 IDM；仅允许显式 Mock')
        if not payload.get('current_image') or not payload.get('target_image'):
            raise ValueError('Mock 也需要独立指定当前观测和目标观测；对齐参考图不会自动作为目标')
        state = payload.get('state')
        if not isinstance(state, list) or not state or len(state) > 128 or any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in state
        ):
            raise ValueError('state 必须是 1～128 维有限数值数组')
        return {'status': 'success', 'mock': True, 'inputs': payload,
                'predicted_action': [0.0] * len(state),
                'action_semantics': 'MOCK zeros; no physical units or robot meaning',
                'latency_ms': (time.perf_counter() - started) * 1000,
                'metrics': None}

"""Read-only runtime audit. Never discovers devices or opens robot/camera connections."""
import argparse
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OWNED_FILES = ('backend/server.py', 'backend/camera_server.py', 'adapters/realsense.py',
               'backend/robot_replay.py', 'adapters/replay.py',
               'run.sh', 'run_camera.sh', 'frontend/index.html', 'frontend/app.js',
               'frontend/replay.mjs', 'frontend/comparison.mjs')


def check(hardware=False):
    results = [(path, (ROOT/path).is_file()) for path in OWNED_FILES]
    modules = ['PIL', 'numpy', 'cv2'] + (['lerobot', 'pyrealsense2', 'xarm'] if hardware else [])
    results += [('Python package: '+module, importlib.util.find_spec(module) is not None) for module in modules]
    return results


def main():
    parser = argparse.ArgumentParser(description='只读检查源码和 Python 依赖，不连接任何硬件')
    parser.add_argument('--hardware', action='store_true', help='同时检查 SDK 是否安装（不扫描设备）')
    args = parser.parse_args()
    results = check(args.hardware)
    print(f'Console root: {ROOT}\nPython: {sys.executable}')
    for name, exists in results:
        print(f'{"OK" if exists else "MISSING"}: {name}')
    return 0 if all(exists for _, exists in results) else 1


if __name__ == '__main__':
    sys.exit(main())

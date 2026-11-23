"""测试共用工具：以隔离方式把仓库根目录加入 sys.path 并装载资料。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from posthist.catalog import load_catalog  # noqa: E402

DATA = ROOT / 'data' / 'records.json'


def load():
    return load_catalog(DATA)

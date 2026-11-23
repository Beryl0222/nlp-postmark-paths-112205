#!/usr/bin/env python3
"""命令行启动器：在隔离模式（-I）下把仓库根目录加入 sys.path。

用法：
    python3 -I posthist_cli.py verify --cover K1
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from posthist.cli import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""프로젝트 루트에서 바로 실행하기 위한 진입점.

  python run.py sources
  python run.py draft --days 7
  python run.py editor
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from trendletter.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

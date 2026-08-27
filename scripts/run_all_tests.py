from __future__ import annotations

import argparse
import inspect
import runpy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def is_real_data_test(function: object) -> bool:
    marks = getattr(function, "pytestmark", [])
    if not isinstance(marks, list):
        marks = [marks]
    return any(getattr(mark, "name", "") == "real_data" for mark in marks)


def main() -> int:
    parser = argparse.ArgumentParser(description="不启用第三方 pytest 插件，顺序执行仓库测试函数")
    parser.add_argument("--include-real-data", action="store_true")
    args = parser.parse_args()
    passed = 0
    skipped = 0
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        namespace = runpy.run_path(str(path))
        for name, function in sorted(namespace.items()):
            if not name.startswith("test_") or not callable(function):
                continue
            if inspect.signature(function).parameters:
                raise RuntimeError(f"{path.name}::{name} 需要 fixture，轻量运行器无法执行")
            if is_real_data_test(function) and not args.include_real_data:
                skipped += 1
                continue
            function()
            passed += 1
            print(f"PASS {path.name}::{name}")
    print(f"TEST_FUNCTIONS_OK passed={passed} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env bash
# loop_gate.sh —— 模块交付自动门 (Definition of Done 的可执行版)
#
# 用法: scripts/loop_gate.sh
# 跑: ① 约束 linter  ② 全量 pytest 回归 + 覆盖率 ≥85% 门 (S8/T51)
# 退出 0 = 全过 (loop 可标 ✅ + commit + push); 非 0 = 有失败 (loop 不标 done, 3 次不过标 ⛔)
#
# 每完成一个模块后调用; 全量回归确保不破坏已 ✅ 的模块。
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== 1/2 约束 linter (check_constraints.py) ==="
python3 scripts/check_constraints.py

echo "=== 2/2 全量回归 pytest + 覆盖率门 (≥85%) ==="
python3 -m pytest -q --cov=semilabs_hone --cov-report=term-missing --cov-fail-under=85

echo "✅ loop_gate 全过: 约束 + 全量测试 + 覆盖率≥85% 均绿"

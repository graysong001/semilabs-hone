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
# 显式拦截退出码: 覆盖率 FAIL 时绝不能滑到 ✅ (2026-07-29 实测漏拦)
# --cov-precision=2 必传: 默认 precision=0 会把 84.53 round 成 85, 退出码判定
# (round(total,0)<85→False) 与 FAIL 打印 (84.53<85→True) 不一致, 假绿放行。
if ! python3 -m pytest -q --cov=semilabs_hone --cov-report=term-missing --cov-fail-under=85 --cov-precision=2; then
    echo "❌ loop_gate 失败: 测试或覆盖率门 (≥85%) 未过"
    exit 1
fi

echo "✅ loop_gate 全过: 约束 + 全量测试 + 覆盖率≥85% 均绿"

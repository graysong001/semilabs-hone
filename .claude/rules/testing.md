---
globs:
  - "**/*Test.java"
  - "**/*.test.ts"
  - "**/*.spec.ts"
---
# 测试质量与场景矩阵

## 🧪 质量硬性指标
- 核心业务逻辑和 API 接口测试覆盖率必须 **≥ 85%**。
- 命名规范: `test_<方法名>_<场景>_<预期结果>`。

## 📐 场景矩阵 (每个核心函数必须覆盖)
1. ✅ **正常流程** (Happy Path)
2. ❌ **异常处理** (Invalid inputs, Null, Exceptions)
3. 📏 **边界条件** (Empty lists, Zero, Max limits)
4. 📈 **极值与并发** (Timeout, Concurrency, Large payloads)

## 🔄 TDD 工作流 (针对复杂逻辑)
1. **先写测试**：根据需求先写包含上述场景的测试用例（此时实现为空或抛异常）。
2. **等待 Review**：展示测试用例给我确认。
3. **再写实现**：我确认后，编写业务代码直到测试全绿 (Red-Green-Refactor)。
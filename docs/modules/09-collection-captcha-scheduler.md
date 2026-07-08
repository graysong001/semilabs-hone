# DM-09 采集-验证码 + 调度（collection/captcha + scheduler）

> 状态：✅ 已完成 | 依赖：DM-05, DM-06 | 设计依据：skim_design.md §10、§12

## 范围
- `semilabs_hone/modules/collection/captcha/{solver,slide_solver,ocr_solver,manual_handler}.py`
- `semilabs_hone/modules/collection/scheduler/{rhythm,warmup}.py`

## 目标
- 验证码：检测类型 → 自动求解（滑块/文字）/ 人工暂停。核心原则：失败 1 次即暂停。
- 节律：安静时段、日限额、随机延迟、暖场浏览。反检测 Layer 6。

## 产出接口契约

### `captcha/solver.py`
```python
async def detect_and_solve(page, ctx) -> SolveResult
# 检测验证码类型 -> 分发到 slide/ocr/manual; 返回 SolveResult{status: solved|paused|failed}
```
### `captcha/slide_solver.py`
```python
async def solve_slide(page) -> bool      # OpenCV 缺口检测 + generate_slide_track 物理轨迹 (DM-06)
```
### `captcha/ocr_solver.py`
```python
async def solve_ocr(image_bytes) -> str  # ddddocr 文字识别
```
### `captcha/manual_handler.py`
```python
async def request_manual_solve(ctx, captcha_type: str, account_id: int) -> None
# 暂停: 写 IPC paused result + ws_events:[captcha_required]; 文案 "请切换到'小红书' Chrome 窗口完成验证"
```
### `scheduler/rhythm.py`
```python
def check_quiet_hours(now=None) -> None            # 22:00-07:00 抛 QuietHoursError
def check_daily_limit(account) -> None             # 超 DAILY_LIMIT_PER_ACCOUNT 抛 DailyLimitError
async def note_delay() -> None                     # 30-90s 随机
async def keyword_delay() -> None                  # 60-180s 随机
def should_pause_for_captcha(fail_count: int) -> bool  # fail_count>=1 即 True
```
### `scheduler/warmup.py`
```python
async def random_browse(page) -> None              # 浏览 2-5 无关页, 每页 30-90s (用 DM-06 random_browse)
```

## 关键约束
- ✅ 自动求解**失败 1 次即暂停**，不硬刚（账号比脚本值钱）。
- ✅ 暂停 = 写 `paused` result + `ws_events:[captcha_required]`，等用户在真 Chrome 完成 → UI 点"已完成" → web 发新 request 唤醒。
- ✅ 节律检查在 collection worker **每个 op 开始时**执行（以实际发起请求为准），非 web 侧。
- ✅ `daily_scrape_count` 按日重置；`fail_count` 成功清零，达 5 自动 suspended。

## 任务清单
- [x] `slide_solver.py`：OpenCV 缺口检测 + DM-06 轨迹拖拽
- [x] `ocr_solver.py`：ddddocr
- [x] `manual_handler.py`：暂停 + WS 通知（经 IPC ws_events）
- [x] `solver.py`：类型检测 + 分发
- [x] `rhythm.py`：check_quiet_hours/check_daily_limit/note_delay/keyword_delay/should_pause_for_captcha
- [x] `warmup.py`：random_browse
- [x] 单测 `tests/collection/test_rhythm.py`：安静时段抛错、日限额、延迟区间、captcha 暂停阈值

## 验收
- 模拟 22:00 → QuietHoursError；日限额满 → DailyLimitError。
- 滑块图给固定样本 → solve_slide 返回缺口距离合理。
- `pytest tests/collection/test_rhythm.py` 绿。

## 实施记录
- 2026-07-09: 全部实现完成。重依赖惰性 import（cv2/ddddocr/playwright 在函数内 import），rhythm 纯 stdlib。
- loop_gate.sh 连续两次 exit 0，契约测试 `test_dm09_captcha_scheduler_contract` 通过。
- 22 个 rhythm 测试全部通过（安静时段/日限额/延迟/captcha 阈值）。
- 约束 linter 通过（无禁止项）。

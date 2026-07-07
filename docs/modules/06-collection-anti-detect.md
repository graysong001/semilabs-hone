# DM-06 采集-反检测（collection/anti_detect）

> 状态：⬜ 未开始　|　依赖：DM-05　|　设计依据：skim_design.md §4.4、§5、§4.3

## 范围
- `semilabs_hone/modules/collection/anti_detect/stealth.py`
- `semilabs_hone/modules/collection/anti_detect/human_behavior.py`
- `semilabs_hone/modules/collection/anti_detect/fingerprint.py`
- `semilabs_hone/modules/collection/anti_detect/ua_pool.py`

## 目标
反检测 Layer 2-4（指纹固定、最小噪声、人类行为）。Layer 1（干净 Chrome）在 DM-05，Layer 5（API 拦截）在 DM-07，Layer 6（节律）在 DM-09。

## 产出接口契约

### `stealth.py`
```python
NOISE_ONLY_SCRIPT: str   # Canvas toDataURL/getImageData + AudioContext getChannelData 微噪声
async def inject_noise(ctx: BrowserContext) -> None   # ctx.add_init_script, 每次导航前
```

### `human_behavior.py`
```python
async def human_type(page, locator, text: str) -> None       # 逐字符 50-200ms, 5% 长停顿
async def human_click(page, locator: dict) -> None           # 贝塞尔曲线鼠标 + 随机偏移
async def random_scroll(page, max_times: int, wait_ms: int) -> None
async def random_browse(page, pages: tuple[int,int]) -> None # 暖场浏览
def generate_slide_track(distance: float) -> list[dict]      # 先加速后减速 + 过冲回弹
```
locator 多策略 dict：`{text?, role?, aria_label?, nth?}`，按优先级回退。

### `fingerprint.py`
```python
class Fingerprint(BaseModel):
    viewport: dict; color_scheme: str; timezone: str; locale: str
def assign_fingerprint() -> Fingerprint        # 一次性随机, 永久固定 (不随机化)
def load_fingerprint(account) -> Fingerprint   # 从 accounts 表读
def apply_fingerprint(ctx, fp: Fingerprint) -> None   # viewport/color-scheme/locale/timezone
```

### `ua_pool.py`
```python
async def get_ua(ctx, account) -> str
# config.UA_STRATEGY=="real" (默认): page.evaluate("navigator.userAgent"), 不覆盖不伪造
# =="variety": 从 config.UA_REMOTE_URL 抓取 + 缓存 data/collection/ua_pool.json (TTL 24h)
#   过滤匹配本机 Chrome major version; bundled 静态列表仅离线兜底 (打 stale 标记)
```

## 关键约束（负面）
- ❌ 不伪造 WebGL（`getParameter`/`getExtension`）。
- ❌ 不覆盖 `navigator.webdriver`/`plugins`/`languages`。
- ❌ 不引入 `playwright-stealth`。
- ✅ Canvas/Audio 噪声要"微小"——破坏指纹稳定性但不影响视觉。
- ✅ 指纹**一次性固定不随机化**（避免异设备登录风控）。UA 默认真实 Chrome UA。
- ✅ `apply_fingerprint` 不设 UA（UA 由 `get_ua` 真实读取，不覆盖）。

## 任务清单
- [ ] `stealth.py`：NOISE_ONLY_SCRIPT（Canvas+Audio 微噪声）+ inject_noise
- [ ] `human_behavior.py`：human_type/human_click（贝塞尔）/random_scroll/random_browse/generate_slide_track
- [ ] `fingerprint.py`：Fingerprint + assign/load/apply
- [ ] `ua_pool.py`：get_ua（real 默认 + variety 远程抓取缓存）
- [ ] accounts 表写回指纹（assign 时 update，配合 DM-02）
- [ ] 单测 `tests/collection/test_human_behavior.py`：轨迹长度/延迟区间；`test_fingerprint.py`：固定性

## 验收
- 注入噪声后，Canvas `toDataURL()` 两次结果不同；`navigator.webdriver` 仍 undefined；WebGL `getParameter` 返回真实 GPU。
- human_type 逐字符、延迟在 50-200ms。
- `get_ua` 返回值 = 本机真实 Chrome UA。

## 实施记录
- （待填）

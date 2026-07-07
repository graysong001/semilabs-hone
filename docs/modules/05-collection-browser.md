# DM-05 采集-浏览器进程（collection/browser）

> 状态：⬜ 未开始　|　依赖：DM-01, DM-03　|　设计依据：skim_design.md §1、§3、§4

## 范围
- `semilabs_hone/modules/collection/browser/cdp.py`
- `semilabs_hone/modules/collection/browser/profile.py`
- `semilabs_hone/modules/collection/browser/launchagent.py`
- `semilabs_hone/modules/collection/browser/worker_main.py`
- `semilabs_hone/modules/collection/manifest.py`（补全 WORKER_ENTRY）

## 目标
macOS 真实 Chrome + CDP 接管的浏览器 worker。这是反检测 Layer 1 的落地，也是 collection worker 进程入口。

## 产出接口契约

### `cdp.py`
```python
def launch_real_chrome(profile_dir: str, port: int) -> subprocess.Popen
async def attach(port: int) -> tuple[Browser, BrowserContext]
def find_free_port() -> int          # 探测 CDP_PORT_RANGE (9333-9340), 区分自占/他占
```

### `profile.py`
```python
def profile_dir_for(account_id: int) -> Path   # data/collection/profiles/<id>/
def ensure_profile(account_id: int) -> Path
```

### `launchagent.py`
```python
def write_plist(account_id: int) -> Path       # com.semilabs.collection-worker, Aqua session
# MVP 默认不启用 (on-demand Popen); 本文件只提供生成能力
```

### `worker_main.py`
```python
def main(argv=None) -> int
# 1) 读 account_id -> ensure_profile -> find_free_port -> launch_real_chrome -> attach
# 2) 注入 stealth 噪声 (DM-06, 可后补; 本模块先留 hook)
# 3) 注册 collection handlers (DM-11, 可后补; 本模块先跑空 handler 表)
# 4) 调 core.ipc.server.serve_worker(module="collection", handlers, on_progress)
```

## 关键约束（负面，不可妥协）
- ❌ 禁止 Playwright `launch()`/`launch_persistent_context()`。只用 `subprocess.Popen` + `connect_over_cdp`。
- ❌ 禁止任何自动化特征参数（`--disable-blink-features=AutomationControlled`/`--enable-automation`/`--no-sandbox`）。Chrome args **仅** `--remote-debugging-port` + `--user-data-dir`。
- ✅ `navigator.webdriver` 必须为 `undefined`（验收时验证）。
- ✅ worker 全程持有 Chrome+ctx，**不向 web 暴露 page 对象**（D9：BrowserPool 概念消失）。
- ✅ 端口冲突区分"自己旧 worker 占"（复用）vs"别的程序占"（换端口）。
- ✅ 空闲超时 `WORKER_IDLE_TIMEOUT`(600s) 自动退出，按需重启。

## 任务清单
- [ ] `cdp.py`：launch_real_chrome（仅两参数）+ attach + find_free_port
- [ ] `profile.py`：profile_dir_for + ensure_profile
- [ ] `launchagent.py`：plist 模板（MVP 不启用，仅生成）
- [ ] `worker_main.py`：拉起→attach→（hook 注入 stealth/handlers）→serve_worker
- [ ] CLI `worker --module collection` 调 worker_main.main（见 cli.py TODO）
- [ ] 反检测自检脚本：attach 后 `page.evaluate("navigator.userAgent")` + `navigator.webdriver`，断言 undefined
- [ ] 单测 `tests/collection/test_cdp.py`：find_free_port、port 冲突逻辑（mock subprocess）

## 验收
- `python -m semilabs_hone worker --module collection --account 1` 能拉起真 Chrome、attach 成功、跑空 IPC 主循环。
- DevTools console `navigator.webdriver` === `undefined`。
- 关闭 Chrome → worker 写 BrowserClosedError result（经 IPC server 异常路径）。

## 依赖说明
- 本模块给 DM-06（stealth 注入）和 DM-11（handlers）留 hook：`worker_main` 在 attach 后调 `anti_detect.stealth.inject_noise(ctx)`、在 serve_worker 前注册 `handlers.build_registry()`。这两个模块未就绪时用 no-op stub，不阻塞本模块验收。

## 实施记录
- （待填）

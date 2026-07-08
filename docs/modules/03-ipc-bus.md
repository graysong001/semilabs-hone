# DM-03 IPC 任务总线（core/ipc）

> 状态：✅ 已完成 | 依赖：DM-01 | 设计依据：skim_design.md §6

## 范围
- `semilabs_hone/core/ipc/protocol.py`
- `semilabs_hone/core/ipc/paths.py`
- `semilabs_hone/core/ipc/client.py`
- `semilabs_hone/core/ipc/server.py`

## 目标
全厂跨进程文件队列。web（client）写 request/轮询 result；worker（server）取 request/写 result+progress。任何模块 worker 复用同一机制。

## 产出接口契约

### `protocol.py`（Pydantic，§6.2）
```python
class IPCRequest(BaseModel):
    request_id: str; module: str; op: str
    account_id: int | None = None; payload: dict; created_at: float
class IPCProgress(BaseModel):
    request_id: str; message: str; data: dict | None; updated_at: float
class IPCResult(BaseModel):
    request_id: str; status: Literal["ok","error","paused","cancelled"]
    data: dict | None = None; error: dict | None = None
    ws_events: list[dict] | None = None; completed_at: float
```

### `paths.py`
`REQUESTS_DIR`/`RESULTS_DIR`/`PROGRESS_DIR`/`CONTROL_CANCEL_DIR` 常量 + `request_path(id)`/`result_path(id)`/`progress_path(id)`/`cancel_sentinel(id)`。

### `client.py`（web 侧）
```python
class IPCClient:
    def submit(self, req: IPCRequest) -> str           # 原子写 request, 返回 request_id
    async def poll_progress(self, request_id) -> IPCProgress | None
    async def wait_result(self, request_id, timeout) -> IPCResult   # 轮询 1s
    def cancel(self, request_id) -> None               # 写 cancel 哨兵
```

### `server.py`（worker 侧通用主循环）
```python
async def serve_worker(module: str, handler_registry: dict[str, Callable],
                       on_progress: Callable[[str, str, dict], None]) -> None
# 轮询 requests/ 取最早且 module 匹配 -> 查 handler 表分发 -> 流式写 progress
# 自检 cancel 哨兵 -> 写 cancelled result; 异常 -> 写 error result (含 category/fix_hint)
```
原子写工具：`atomic_write_json(path, obj)`（`.tmp`→`os.rename`）。

## 关键约束
- worker **不直连 WebSocket**：进度经 `ws_events`（终态）+ progress 文件（流式覆盖）由 client 代广播。
- 写入必须原子（`.tmp`→`os.rename`），避免读到半截 JSON。
- result 被 client 取走后由 client 删；定期 gc 超 1h 孤儿文件。
- cancel 用哨兵文件，worker 每步自检。

## 任务清单
- [x] `protocol.py` 三 schema
- [x] `paths.py` 路径常量 + 辅助
- [x] `atomic_write_json` + `read_json_if_exists` 工具
- [x] `client.py`：submit/poll_progress/wait_result/cancel
- [x] `server.py`：serve_worker 主循环（轮询/分发/progress/cancel/result）
- [ ] 孤儿文件 gc（超 1h 清理）
- [x] 单测 `tests/core/test_ipc.py`：submit→result 端到端、cancel、原子写、progress 流式

## 验收
- 写一个 echo handler：client.submit → server 收到 → 回 result，client.wait_result 拿到。
- `pytest tests/core/test_ipc.py` 绿。

## 实施记录
- 2026-07-09: 实现 protocol.py（三类 schema）、paths.py（惰性读 config 路径常量 + atomic_write_json）、client.py（IPCClient 四方法）、server.py（serve_worker 主循环）、`__init__.py`、test_ipc.py（30 单测覆盖协议/路径/原子写/client/server/端到端/cancel/错误分类）。`loop_gate.sh` 连续两次 exit 0。契约测试 `test_dm03_ipc_contract` 通过。

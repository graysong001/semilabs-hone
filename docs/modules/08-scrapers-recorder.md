# DM-08 采集-录制器 + LLM 映射（collection/scrapers/recorder 侧）

> 状态：🔄 代码完成待人工验　|　依赖：DM-05, DM-07　|　设计依据：skim_design.md §8.3、§8.4、§19　|　**技术难点，建议单独完整会话攻坚**

## 范围
- `semilabs_hone/modules/collection/scrapers/recorder.py`
- `semilabs_hone/modules/collection/scrapers/llm_mapper.py`
- 产物：`platforms/xiaohongshu/platform.yaml`（录制+LLM 生成，覆盖 DM-07 的占位）

## 目标
把"加一个网站"从写配置变成"在真 Chrome 点一遍"：recorder 捕获 step 链 + 数据 XHR 样本，LLM(Haiku) 把样本映射到 schema group 生成 JSONPath，写回 platform.yaml。

## 产出接口契约

### `recorder.py`
```python
class RecordingSession:
    async def start(self, base_url: str) -> None        # 开 Chrome, 注入 CDP 监听
    async def stop(self) -> RecordingResult
    # 期间: 捕获 navigate/input/click/scroll 为 step; 点击元素捕获多策略 selector
    #       (text/role/aria-label/nth-of-type); 记录每个响应 url/method/JSON 样本
    # 启发式标注: 操作后短时间内到达的大体积 JSON = 数据 XHR
class RecordingResult:
    flows: dict[str, list[Step]]      # search/detail/comments (由 UI 分段标注)
    xhr_samples: dict[str, dict]      # save_as -> JSON 样本
async def record_platform(display_name: str, base_url: str) -> RecordingResult
```
UI 步骤编辑器（DM-11 实现 UI）调 recorder API：删闲步、标 `{keyword}` 占位符、指派 XHR→group。

### `llm_mapper.py`
```python
async def map_group(sample_json: dict, group: str,
                    field_specs: dict[str,str]) -> dict[str, str]
# Haiku 结构化输出: group 各字段 -> JSONPath; field_specs 含字段名+自然语言说明
async def extract_custom_field(sample_json: dict, description: str) -> str
# 自然语言描述 -> JSONPath
def validate_map(sample_json: dict, field_map: dict[str,str]) -> dict[str,bool]
# 用样本跑一遍 JSONPath, 字段非空才算通过; 失败标红
def build_platform_yaml(display_name, base_url, flows, maps) -> str
# 组装 platform.yaml 文本
```
模型：`config.LLM_MODEL` = `claude-haiku-4-5-20251001`，anthropic SDK 结构化输出。

## 关键约束
- 全程**不写 YAML、不碰 DevTools、不写 JSONPath**——用户只点页面 + 指派 XHR→group + 填自然语言描述。
- LLM 映射必须过**样本校验**（用样本 JSON 跑 JSONPath 非空），失败让 LLM 重试或标红人工确认。
- 映射只在**录制时**调 LLM 一次；运行时不调（DM-07 的轻兜底除外）。
- 点击 selector 捕获**多策略**，运行时按优先级回退降脆弱。

## 任务清单
- [x] `recorder.py`：CDP 事件监听（Page/Input/Network）→ step 捕获 + 多策略 selector
- [x] `recorder.py`：XHR 样本记录 + 启发式标注（操作时序对齐）
- [x] `recorder.py`：RecordingSession/Result + record_platform
- [x] `llm_mapper.py`：map_group（Haiku 结构化输出）+ validate_map（样本校验+重试）
- [x] `llm_mapper.py`：extract_custom_field + build_platform_yaml
- [ ] 端到端：录制 XHS search/detail/comments → LLM 生成 → 写 platforms/xiaohongshu/platform.yaml
- [ ] 用 DM-07 engine 跑生成的 yaml，验证 search 返回 ItemRef
- [x] 单测 `tests/collection/test_llm_mapper.py`：mock anthropic，验证 map_group 结构化输出 + validate_map

## 验收
- 录制 XHS 三条 flow → 生成 platform.yaml → engine 用它跑 search 拿到 ItemRef 列表（字段非空）。
- 加一个陌生 SPA 站点：录制→指派→生成→可抓，全程零手写配置。
- 自定义字段（如"视频时长"）填一句描述能定位生成 JSONPath。

## 风险与备注
- CDP 录制点击的多策略 selector 运行时回退优先级需实测调（skim_design.md §21 未决）。
- 纯 SSR/无 XHR 站点走 `wait_selector`+`extract_dom`+HTML→LLM，降级路径，MVP 不优先。
- 本模块是加站体验的关键，验收务必用一个**陌生**站点（非 XHS）验证零手写。

## 实施记录
- recorder.py + llm_mapper.py + test_llm_mapper.py 代码+mock 测完成；真录制待人工
  commit: 7e79588

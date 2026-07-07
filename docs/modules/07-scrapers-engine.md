# DM-07 采集-抓取引擎运行时（collection/scrapers/engine 侧）

> 状态：⬜ 未开始　|　依赖：DM-05, DM-06　|　设计依据：skim_design.md §5.2、§8.1、§8.2、§8.5、§8.7、§8.8

## 范围
- `semilabs_hone/modules/collection/scrapers/base.py`
- `semilabs_hone/modules/collection/scrapers/spec.py`
- `semilabs_hone/modules/collection/scrapers/field_extract.py`
- `semilabs_hone/modules/collection/scrapers/engine.py`
- `semilabs_hone/modules/collection/scrapers/registry.py`

> 录制器/LLM 映射器在 DM-08（独立，技术难点）。本模块只做**运行时**：读 platform.yaml → 回放 step 链 → JSONPath 提取。本模块可用一份**手写或占位**的 XHS platform.yaml 验收（DM-08 会用录制重新生成）。

## 目标
通用引擎：平台无关地回放 flow 的 step 链，拦截 XHR、按 group 映射抽字段、Pydantic 校验、失败轻兜底。

## 产出接口契约

### `base.py`
```python
class BasePlatformScraper(ABC):
    async def search(self, keyword, sort) -> list[ItemRef]: ...
    async def fetch_item(self, ref: ItemRef) -> ScrapedPost: ...
    async def fetch_comments(self, ref: ItemRef) -> list[ScrapedComment]: ...
    async def login(self) -> LoginResult: ...
# ItemRef / ScrapedPost / ScrapedComment 在 core/models/schemas (DM-02)
# schema group 常量: GROUP_ITEM_REF / GROUP_POST_BODY / GROUP_POST_INTERACTIONS / GROUP_COMMENTS
```

### `spec.py`（PlatformSpec，platform.yaml 的 pydantic 模型，§8.2）
```python
class Step(BaseModel): type: Literal["navigate","input","click","scroll","wait_xhr","extract","wait_selector"]; ...
class Flow(BaseModel): steps: list[Step]
class PlatformSpec(BaseModel):
    platform: str; display_name: str; base_url: str
    login: LoginSpec; flows: dict[str, Flow]; sort_values: dict[str,str]
```

### `field_extract.py`
```python
def extract_api(sample_json: dict, group: str, field_map: dict[str,str]) -> list[dict]
# jsonpath-ng 按 list_path 取列表, 按 fields JSONPath 取字段; 缺字段用默认值不崩
def extract_dom(page, group: str, field_map: dict[str,str]) -> list[dict]
# css:<sel> 取 text, css:<sel>@<attr> 取属性, xpath:<expr> 取节点 (selectolax)
def render_template(tpl: str, **vars) -> str   # {keyword} 占位符渲染
```

### `engine.py`（§8.5）
```python
class GenericEngine(BasePlatformScraper):
    def __init__(self, spec: PlatformSpec, ctx, account): ...
    async def run_flow(self, flow_name, **vars) -> list
    # navigate/input/click/scroll 用 DM-06 human_behavior; wait_xhr 用 page.on("response")+Future+wait_for;
    # extract 调 field_extract; 失败轻兜底 (见下)
```
**轻兜底**：某 group Pydantic 校验失败 → 仅对该条该 group 回退 LLM 直接抽（Haiku，喂原始 JSON+schema），结果**只用于本次不回写 yaml**；连续多条失败 → 返回标志让 handler 发 WS"建议重新录制 <flow>"。

### `registry.py`
```python
def load_registry() -> dict[str, tuple[PlatformSpec, type[BasePlatformScraper] | None]]
def list_platforms() -> list[str]          # UI 下拉用
def get(platform: str) -> tuple[...]       # op 路由用
# 启动 glob platforms/*/platform.yaml -> 加载校验 -> adapter.py 若存在则覆盖 engine
```

## 关键约束
- 运行时**纯 JSONPath，不调 LLM**（快、免费、确定）；仅失败兜底才调 LLM，且不回写映射、不做自动愈合。
- API 拦截优先（`page.on("response")`+`Future`+`wait_for` 超时兜底 DOM），不自构请求。
- 字段提取缺字段用默认值，不崩。
- adapter 可缺省（XHS 走纯 yaml）；adapter 调 `super()` 复用 engine。

## 任务清单
- [ ] `base.py`：ABC + schema group 常量
- [ ] `spec.py`：PlatformSpec/Flow/Step/LoginSpec pydantic（对应 §8.2 yaml）
- [ ] `field_extract.py`：extract_api（jsonpath-ng）+ extract_dom（selectolax）+ render_template
- [ ] `engine.py`：run_flow（回放 step 链，人类行为+API 拦截+提取+校验+轻兜底）
- [ ] `registry.py`：load/list/get + adapter 覆盖
- [ ] 占位 `platforms/xiaohongshu/platform.yaml`（手写一份，供本模块验收；DM-08 录制后覆盖）
- [ ] 单测 `tests/collection/test_field_extract.py`：JSONPath/CSS 取值、空/畸形 JSON；`test_engine.py`：mock page 跑 run_flow

## 验收
- 用占位 XHS platform.yaml + mock page，`engine.search("咖啡","general")` 返回 list[ItemRef]。
- 字段缺失不崩，给默认值。
- `registry.list_platforms()` 含 xiaohongshu。

## 实施记录
- （待填）

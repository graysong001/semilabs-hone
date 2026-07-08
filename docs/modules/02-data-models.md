# DM-02 数据模型（models / schemas）

> 状态：✅ 已完成　|　依赖：DM-01　|　设计依据：skim_design.md §7、§13.3、spec §4

## 范围
- `semilabs_hone/core/models/db.py`
- `semilabs_hone/core/models/account.py` `keyword.py` `task.py` `post.py` `comment.py`
- `semilabs_hone/core/models/schemas.py`

## 目标
共享 SQLite 的 ORM 表 + Pydantic I/O schema。天然 platform-agnostic（`platform` 字段贯穿）。

## 产出接口契约

### `db.py`
```python
Engine: Engine                       # sqlalchemy 2, sqlite:///data/factory.db
def init_db() -> None                # create_all
def get_session() -> Session         # 会话工厂
```

### ORM 表（spec §4 + §7 修订）
- `Account`：spec §4.1 + 补 `color_scheme`/`timezone`/`locale`（§7.2）。**UA 不入库**。
- `Keyword`：spec §4.2，`UNIQUE(text, platform)`。
- `ScrapeTask`：spec §4.3 + 补 `download_images`/`collect_comments` 两列（§7.1，D6 修复）。
- `TaskKeyword`：spec §4.4，复合主键。
- `Post`：spec §4.5，`UNIQUE(platform, platform_id)` upsert，`raw_json` 保留。
- `Comment`：spec §4.6，`UNIQUE(post_id, platform_id)`。

### `schemas.py`（Pydantic v2）
- API I/O：`AccountCreate{platform,nickname}`、`TaskCreate{account_id,platform,keywords[],sort,max_posts_per_keyword,download_images,collect_comments}`、`TaskStatus` 等。
- WS 契约：`ProgressMessage`（§13.3，含 `module`/`data` 字段，D7/D8 修复）。
- 抓取管线数据类：`ItemRef{platform,item_id,title,author_name,likes?}`、`ScrapedPost`、`ScrapedComment`（区别于 ORM，供 engine 输出）。

## 关键约束
- `platform_id` 去重 upsert；`raw_json` 必须保留（为 AI 分析预留）；`last_note_index` 断点续传。
- 不生成 `DROP TABLE`/`TRUNCATE`。迁移只新增脚本（见 .claude/rules/database.md）。
- ORM 与 Pydantic 分离：engine 输出 `ScrapedPost`，handlers 负责转 ORM upsert。

## 任务清单
- [x] `db.py`：Engine + init_db + get_session
- [x] 6 张 ORM 表（含 §7.1/7.2 补字段）
- [x] `schemas.py`：AccountCreate/TaskCreate/ProgressMessage/ItemRef/ScrapedPost/ScrapedComment
- [x] `init_db()` 跑通，生成 data/factory.db
- [x] 单测 `tests/core/test_models.py`：默认值、upsert 二次更新、task 生命周期、resume 保留 last_note_index

## 验收
- `python -c "from semilabs_hone.core.models.db import init_db; init_db()"` 生成 db。
- `pytest tests/core/test_models.py` 绿。

## 实施记录
- DM-02: 完成 6 张 ORM 表 + Pydantic schemas + 24 个单测。loop_gate.sh 退出 0。commit: (待提交)

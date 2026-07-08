# DM-10 采集-导出 + 图片磁盘（collection/export + core/utils/image_downloader）

> 状态：✅ 已完成　|　依赖：DM-02 ✅　|　设计依据：skim_design.md §14、§15

## 范围
- `semilabs_hone/modules/collection/export/csv_exporter.py`
- `semilabs_hone/core/utils/image_downloader.py`

## 目标
- CSV 导出：AI 模式（单文件含评论）+ Excel 模式（ZIP 两 CSV），直接读 SQLite，不依赖 worker。
- 图片下载：异步下载 + 磁盘报警（30GB）。

## 产出接口契约

### `csv_exporter.py`
```python
def export_csv(task_id: int | None, keyword: str | None, fmt: str) -> Path
# fmt: "ai" | "excel"
# ai:    单文件, top_comments = "作者:内容(N likes)" 管道符分隔
# excel: ZIP 含 posts.csv + comments.csv, 按 note_id 关联
def export_empty_db(fmt: str) -> Path   # 空库不崩
```
AI 模式字段：note_id/url/title/author/content/tags(管道符)/post_type/likes/collects/comments_count/shares/published_at/keyword/image_count/top_comments/scraped_at。

### `image_downloader.py`
```python
async def download_images(urls: list[str], note_id: str) -> list[Path]
# 落 data/collection/images/<note_id>/; max_concurrency=4
async def check_disk() -> DiskStatus
# du 统计 images 目录; IMAGE_DISK_WARN_GB=30 -> 广播 disk_warn (不中断)
# IMAGE_DISK_STOP_GB (默认 None=关) -> 超 threshold 抛 DiskFullError 停下载
# shutil.disk_usage 剩余 <2GB 也 warn
```
disk_warn 经 IPC progress 文件由 client 代广播（worker 侧），或直接调 WSManager（web 侧导出时）。

## 关键约束
- 导出直接读 SQLite，**不依赖 worker**（导出是同步操作）。
- 30GB 报警**不中断**任务；硬停阈值默认关，可配。
- 空库导出不崩。
- 图片下载失败（单张）不阻断整篇，记 warn。

## 任务清单
- [x] `csv_exporter.py`：AI 模式（top_comments 管道符）+ Excel 模式（ZIP 两 CSV）
- [x] `csv_exporter.py`：空库处理 + 按 task_id/keyword 筛选
- [x] `image_downloader.py`：异步下载（max_concurrency=4）+ 磁盘检查
- [x] `image_downloader.py`：30GB warn + 可配硬停 + 剩余空间检查
- [x] 单测 `tests/collection/test_csv_export.py`：AI 格式列头/top_comments、Excel ZIP 两 CSV、空库；image_downloader：磁盘阈值 mock

## 验收
- 造数据 → AI CSV `top_comments` = `作者:内容(N likes)` 管道符；Excel ZIP 解压含 posts.csv+comments.csv。
- 空库导出返回空文件不崩。
- images 目录 >30GB（mock）→ 触发 disk_warn 事件。

## 实施记录
- （待填）

# semilabs-hone

内容工厂 — 多平台内容素材采集与分析系统。单体仓库，共享 `core/` + 多业务 `modules/`。

- **modules/collection**（信息采集，UI 展示名 Skim）：macOS 真实 Chrome + CDP 接管，反检测六层，file IPC 进程解耦，录制+LLM 加站。
- **modules/analysis | production | operations**：预留（AI 分析 / 内容制作 / 内容运营）。

完整设计见 [docs/skim_design.md](docs/skim_design.md)。

## 快速开始

```bash
pip install -e ".[dev]"
python -m semilabs_hone version
python -m semilabs_hone serve --port 8530      # TODO: 见 docs/skim_design.md §13
python -m semilabs_hone worker --module collection  # TODO: 见 docs/skim_design.md §6
```

## 目录

- `semilabs_hone/core/` — 共享层：ipc / models / ui / utils
- `semilabs_hone/modules/collection/` — 采集模块：browser / anti_detect / scrapers / captcha / scheduler / export
- `data/` — 运行时数据（gitignored）
- `tests/` — core + collection

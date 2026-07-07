# CLAUDE.local.md - 个人本地偏好

## 交互习惯
- 当我要求"压缩上下文"时，请默认帮我保留：1. 当前的数据库表结构设计；2. 前后端 API 契约（Interface/DTO）；3. 下一步的执行计划。丢弃所有的探索过程和报错日志。
- 在生成大段代码前，先用一句话总结你要写的核心逻辑，确认我没走神。

## 本地环境说明
- 数据库：本地 SQLite 单文件 `data/factory.db`（各模块共享，零运维）。
- Web 外壳运行在 127.0.0.1:8530（FastAPI + Uvicorn）。
- 采集浏览器 worker 由 web 按需 `subprocess.Popen` 拉起，CDP 端口 9333-9340。
- 目标平台 macOS，原生 Chrome 经 `--remote-debugging-port` 接管（非 Playwright launch）。

> 注：本文件环境段已从原始 ruleset（MySQL Docker 3306 / 前端 3000 / 后端 8080）适配为 semilabs-hone 实际栈。

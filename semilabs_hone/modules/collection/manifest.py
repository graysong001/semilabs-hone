"""collection 模块 manifest — 模块元信息 + 路由注册表 + worker 入口。

core/ui/app.py 启动时遍历 modules/*/manifest.py 注册路由。
设计见 docs/skim_design.md §2、§13.1。
"""

NAME = "Skim 采集"          # UI 展示名
MODULE_ID = "collection"    # IPC module 字段

# 路由注册表 (core/ui 外壳挂载)
ROUTES = [
    "semilabs_hone.modules.collection.routes.accounts",
    "semilabs_hone.modules.collection.routes.tasks",
    "semilabs_hone.modules.collection.routes.posts",
    "semilabs_hone.modules.collection.routes.export",
]

# worker 入口 (core/ipc/server 主循环 + 本模块 handler)
WORKER_ENTRY = "semilabs_hone.modules.collection.browser.worker_main"

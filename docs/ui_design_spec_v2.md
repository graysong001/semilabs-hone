合并版文档太长，我直接在对话中完整输出。以下是融合两版设计所长后的 **完整 UI 设计规范**：
---
# 🎨 内容工厂 UI 设计与布局规范 (V2 合并版)
> **Agent 阅读提示**：本规范取 V1 的完整工程代码（Jinja2/HTMX/JS）与 V2 的暗色科技风（配色/四大板块/顶栏/统计卡片/风控接管页）融合。技术栈为 `FastAPI + Jinja2 + HTMX + Tailwind CSS (CDN)`。
---
## 一、两版取长补短总结
| 维度 | V1 (亮色Meher风) | V2 (暗色科技风) | 合并版取舍 |
|------|-----------------|---------------|-----------|
| **配色** | 蓝紫主色 + 白底 | 暗色底 + 蓝绿红状态色 | ✅ 采用 V2 暗色系，适合长时间挂机 |
| **CSS框架** | 原生CSS ~400行 | Tailwind CDN | ✅ 采用 Tailwind，开发效率更高 |
| **侧边栏** | 2组(采集+预留) | 4大业务板块 | ✅ 采用 V2 四模块，完整展示产品全貌 |
| **顶栏** | 无独立顶栏 | 搜索+心跳+头像 | ✅ 采用 V2 顶栏设计 |
| **统计卡片** | 无 | 三色渐变卡片 | ✅ 新增，补齐信息密度 |
| **风控接管页** | 无 | 左右分栏全屏 | ✅ 新增，关键交互场景 |
| **HTMX模式** | 5s局部刷新+Partial | 未定义 | ✅ 保留 V1 的完整 HTMX 模式 |
| **状态徽章** | 完整6种状态+闪烁 | 仅概念描述 | ✅ 保留 V1 完整组件，适配暗色 |
| **进度条** | 完整组件 | 未定义 | ✅ 保留 V1 组件，适配暗色 |
| **弹窗交互** | 二次确认(耗时预估) | 仅概念 | ✅ 保留 V1 完整流程 |
| **数据预览** | 行展开+评论子表格 | 缩略图+热度指示 | ✅ 融合：行展开+绿色热度圆点 |
| **JS脚本** | 完整 ~120行 | 未定义 | ✅ 保留 V1 全部逻辑 |
---
## 二、配色体系 (CSS变量 + Tailwind映射)
```css
:root {
  /* ===== 基础背景色 (暗黑科技风) ===== */
  --bg-app:        #1B202B;   /* 主背景/侧边栏 (tailwind: bg-slate-900 自定义) */
  --bg-content:    #222733;   /* 内容区背景 */
  --bg-card:       #2A303D;   /* 卡片背景 (tailwind: bg-slate-800) */
  --bg-card-hover: #313847;   /* 卡片悬停 */
  --bg-input:      #1E242F;   /* 输入框背景 */
  /* ===== 边框 ===== */
  --border:        #374151;   /* 标准边框 (tailwind: border-gray-700) */
  /* ===== 文字 ===== */
  --text-primary:   #FFFFFF;  /* 标题 */
  --text-body:      #D1D5DB;  /* 正文 (tailwind: text-gray-300) */
  --text-secondary: #9CA3AF;  /* 次要文字 (tailwind: text-gray-400) */
  --text-muted:     #6B7280;  /* 禁用/占位 (tailwind: text-gray-500) */
  /* ===== 品牌与交互色 ===== */
  --primary:        #007BFF;  /* 主动作色 (蓝) */
  --primary-light:  #1A8CFF;
  --primary-dark:   #0066D6;
  /* ===== 状态色 ===== */
  --success:       #00E396;  /* 运行中/完成 (翠绿) */
  --warning:       #FEB019;  /* 排队/暖场 (橘黄) */
  --danger:        #FF4560;  /* 风控/错误 (珊瑚红) */
  --info:          #722ED1;  /* 异常中止 (紫) */
  --neutral:       #6B7280;  /* 暂停/夜间 (灰) */
  /* ===== 风控接管页渐变 ===== */
  --gradient-warm: linear-gradient(135deg, #FF6B35 0%, #FF4560 100%);
  /* ===== 布局 ===== */
  --sidebar-width: 240px;
  --topbar-height: 60px;
  --radius-md: 10px;
  --shadow-card: 0 2px 8px rgba(0,0,0,0.25);
  --shadow-modal: 0 20px 60px rgba(0,0,0,0.5);
}
```
### Tailwind CDN 配置注入
```html
<script src="https://cdn.tailwindcss.com"></script>
<script>
  tailwind.config = {
    theme: {
      extend: {
        colors: {
          'app':     { DEFAULT: '#1B202B', content: '#222733', card: '#2A303D', hover: '#313847', input: '#1E242F' },
          'brand':   { DEFAULT: '#007BFF', light: '#1A8CFF', dark: '#0066D6' },
          'success': { DEFAULT: '#00E396' },
          'warning': { DEFAULT: '#FEB019' },
          'danger':  { DEFAULT: '#FF4560' },
        }
      }
    }
  }
</script>
```
---
## 三、核心布局框架
```
┌──────────────────────────────────────────────────────────────┐
│  [Logo] 内容工厂        [🔍 全局搜索] [🟢 心跳] [👤]          │ ← 顶栏 (60px)
├───────────┬──────────────────────────────────────────────────┤
│           │  📁 素材抓取 > 采集任务大厅                         │ ← 面包屑
│  📌 抓取   ├──────────────────────────────────────────────────┤
│   ├登录配置│  ┌──────┐ ┌──────┐ ┌──────┐                      │
│   └任务大厅│  │今日87│ │运行2 │ │异常1 │  ← 三色统计卡片       │
│  📌 加工   │  └──────┘ └──────┘ └──────┘                      │
│   ├数据资产│  ┌──────────────────────────────────────────┐    │
│   ├意图分析│  │  任务表格 (暗色卡片)                        │    │
│   └提案池  │  │  ☐ 🟢运行中  AI编程  ▓▓▓░░  小红书  ⋮  │    │
│  📌 制作   │  │  ☐ 🔴需验证  赚钱    ▓▓▓▓░  知乎   ⋮  │    │
│   ├图文台  │  └──────────────────────────────────────────┘    │
│   └脚本坊  │  Showing 1 to 10 of 28  < 1 2 3 >               │
│  📌 运营   │                                                  │
│   ├发布    │                                                  │
│   └追踪盘  │                                                  │
│  ──────── │                                                  │
│  🟢 Worker │                                                  │
└───────────┴──────────────────────────────────────────────────┘
   侧边栏240px              剩余空间
```
---
## 四、Base 模板 (含顶栏 + Tailwind配置)
```html
<!-- templates/base.html -->
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>内容工厂 - {{ page_title|default('采集任务大厅') }}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      theme: {
        extend: {
          colors: {
            'app': { DEFAULT: '#1B202B', content: '#222733', card: '#2A303D', hover: '#313847', input: '#1E242F' },
            'brand': { DEFAULT: '#007BFF', light: '#1A8CFF', dark: '#0066D6' },
            'success': { DEFAULT: '#00E396' },
            'warning': { DEFAULT: '#FEB019' },
            'danger': { DEFAULT: '#FF4560' },
          }
        }
      }
    }
  </script>
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
  <style>
    .radio-card.selected { border-color: #007BFF !important; background: rgba(0,123,255,0.1) !important; }
    .count-btn.active { background: #007BFF !important; color: #fff !important; border-color: #007BFF !important; }
    dialog { border: none; }
    dialog::backdrop { background: rgba(0,0,0,0.6); backdrop-filter: blur(4px); }
    @keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #374151; border-radius: 3px; }
  </style>
</head>
<body class="bg-app text-gray-300">
<div class="flex min-h-screen">
  {% include "partials/_sidebar.html" %}
  <div class="flex-1 ml-[240px] flex flex-col min-h-screen">
    <!-- 顶部状态头 -->
    <header class="h-[60px] bg-app border-b border-gray-700/50 flex items-center justify-between px-8 sticky top-0 z-40">
      <div class="flex-1"></div>
      <div class="flex items-center gap-4">
        <!-- 全局搜索 -->
        <div class="relative">
          <svg class="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" width="14" height="14"
               fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="7" cy="7" r="5"/><path d="M15 15l-3-3"/>
          </svg>
          <input type="text" placeholder="Search..."
                 class="w-48 h-9 pl-9 pr-3 bg-app-input border border-gray-700 rounded-lg text-sm
                        text-gray-300 placeholder-gray-500 focus:border-brand focus:outline-none focus:w-64 transition-all">
        </div>
        <!-- Worker 心跳灯 -->
        <div class="flex items-center gap-2 px-3 py-1.5 bg-app-card rounded-lg border border-gray-700">
          <span class="w-2 h-2 rounded-full {% if worker_alive %}bg-green-500 animate-pulse{% else %}bg-red-500{% endif %}"></span>
          <span class="text-xs {{ 'text-green-400' if worker_alive else 'text-red-400' }}">
            {{ 'Engine Online' if worker_alive else 'Engine Offline' }}
          </span>
        </div>
        <!-- 用户头像 -->
        <div class="w-9 h-9 rounded-full bg-brand/20 text-brand flex items-center justify-center text-sm font-bold">U</div>
      </div>
    </header>
    <main class="flex-1 p-8">
      {% block content %}{% endblock %}
    </main>
  </div>
</div>
{% include "partials/_create_task_modal.html" %}
<div id="toast-container" class="fixed top-5 right-5 z-[9999] flex flex-col gap-2"></div>
<script src="/static/js/app.js"></script>
</body>
</html>
```
---
## 五、侧边栏 (四大业务板块完整版)
```html
<!-- templates/partials/_sidebar.html -->
<aside class="w-[240px] bg-app flex flex-col fixed top-0 left-0 bottom-0 z-50">
  <div class="h-[60px] flex items-center gap-2.5 px-5 border-b border-gray-700/50">
    <div class="w-8 h-8 bg-brand rounded-lg flex items-center justify-center text-lg">🏭</div>
    <span class="text-white font-bold text-base">内容工厂</span>
  </div>
  <nav class="flex-1 py-4 overflow-y-auto">
    <!-- 模块一：素材抓取 -->
    <div class="mb-6">
      <div class="px-5 text-[11px] uppercase text-gray-600 tracking-wider mb-2 font-semibold">素材抓取</div>
      <a href="/auth" class="flex items-center gap-3 px-5 py-2.5 text-sm transition-colors
                              {% if active_page == 'auth' %}text-white bg-brand/10 border-l-[3px] border-brand{% else %}text-gray-400 hover:text-white hover:bg-white/5{% endif %}">
        <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/>
        </svg>
        <span>平台登录配置</span>
      </a>
      <a href="/" class="flex items-center gap-3 px-5 py-2.5 text-sm transition-colors
                              {% if active_page == 'dashboard' %}text-white bg-brand/10 border-l-[3px] border-brand{% else %}text-gray-400 hover:text-white hover:bg-white/5{% endif %}">
        <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
          <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
          <rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>
        </svg>
        <span>采集任务大厅</span>
        <span class="ml-auto bg-brand text-white text-[11px] font-semibold px-2 py-0.5 rounded-full min-w-[20px] text-center">{{ running_count }}</span>
      </a>
    </div>
    <!-- 模块二：素材加工 -->
    <div class="mb-6">
      <div class="px-5 text-[11px] uppercase text-gray-600 tracking-wider mb-2 font-semibold">素材加工</div>
      <a href="/data" class="flex items-center gap-3 px-5 py-2.5 text-sm transition-colors
                              {% if active_page == 'data' %}text-white bg-brand/10 border-l-[3px] border-brand{% else %}text-gray-400 hover:text-white hover:bg-white/5{% endif %}">
        <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M3 3v18h18"/><path d="M7 14l4-4 4 4 6-6"/>
        </svg>
        <span>数据资产库</span>
      </a>
      <a class="flex items-center gap-3 px-5 py-2.5 text-sm text-gray-600 cursor-not-allowed opacity-50">
        <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>
        <span>AI 意图分析</span>
        <span class="ml-auto text-[10px] bg-gray-700 text-gray-400 px-1.5 py-0.5 rounded">soon</span>
      </a>
      <a class="flex items-center gap-3 px-5 py-2.5 text-sm text-gray-600 cursor-not-allowed opacity-50">
        <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
        <span>热点提案池</span>
        <span class="ml-auto text-[10px] bg-gray-700 text-gray-400 px-1.5 py-0.5 rounded">soon</span>
      </a>
    </div>
    <!-- 模块三：内容制作 -->
    <div class="mb-6">
      <div class="px-5 text-[11px] uppercase text-gray-600 tracking-wider mb-2 font-semibold">内容制作</div>
      <a class="flex items-center gap-3 px-5 py-2.5 text-sm text-gray-600 cursor-not-allowed opacity-50">
        <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 113 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
        <span>图文生成台</span>
        <span class="ml-auto text-[10px] bg-gray-700 text-gray-400 px-1.5 py-0.5 rounded">soon</span>
      </a>
      <a class="flex items-center gap-3 px-5 py-2.5 text-sm text-gray-600 cursor-not-allowed opacity-50">
        <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>
        <span>视频脚本工坊</span>
        <span class="ml-auto text-[10px] bg-gray-700 text-gray-400 px-1.5 py-0.5 rounded">soon</span>
      </a>
    </div>
    <!-- 模块四：内容运营 -->
    <div class="mb-6">
      <div class="px-5 text-[11px] uppercase text-gray-600 tracking-wider mb-2 font-semibold">内容运营</div>
      <a class="flex items-center gap-3 px-5 py-2.5 text-sm text-gray-600 cursor-not-allowed opacity-50">
        <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>
        <span>分发与发布</span>
        <span class="ml-auto text-[10px] bg-gray-700 text-gray-400 px-1.5 py-0.5 rounded">soon</span>
      </a>
      <a class="flex items-center gap-3 px-5 py-2.5 text-sm text-gray-600 cursor-not-allowed opacity-50">
        <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 6l-9.5 9.5-5-5L1 18"/><path d="M17 6h6v6"/></svg>
        <span>数据追踪盘</span>
        <span class="ml-auto text-[10px] bg-gray-700 text-gray-400 px-1.5 py-0.5 rounded">soon</span>
      </a>
    </div>
  </nav>
  <div class="px-5 py-4 border-t border-gray-700/50">
    <div class="flex items-center gap-2">
      <span class="w-2 h-2 rounded-full {% if worker_alive %}bg-green-500 animate-pulse{% else %}bg-gray-600{% endif %}"></span>
      <span class="text-xs {{ 'text-green-400' if worker_alive else 'text-gray-500' }}">{{ 'Worker 运行中' if worker_alive else 'Worker 离线' }}</span>
    </div>
    <div class="text-[10px] text-gray-600 mt-1">本地引擎 v0.1.0</div>
  </div>
</aside>
```
---
## 六、采集任务大厅 (Dashboard)
### 6.1 统计卡片
```html
<!-- templates/partials/_stat_cards.html -->
<div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
  <div class="bg-app-card rounded-lg p-5 border border-gray-700">
    <div class="flex items-center justify-between mb-2">
      <span class="text-sm text-gray-400">今日抓取量</span>
      <div class="w-9 h-9 rounded-lg flex items-center justify-center bg-blue-500/15 text-blue-400 text-lg">📥</div>
    </div>
    <div class="text-3xl font-bold text-white">{{ today_count }}</div>
    <div class="text-xs text-gray-500 mt-1">上限 {{ daily_limit }} / 剩余 {{ daily_limit - today_count }}</div>
  </div>
  <div class="bg-app-card rounded-lg p-5 border border-gray-700">
    <div class="flex items-center justify-between mb-2">
      <span class="text-sm text-gray-400">运行中任务</span>
      <div class="w-9 h-9 rounded-lg flex items-center justify-center bg-green-500/15 text-green-400 text-lg">🔄</div>
    </div>
    <div class="text-3xl font-bold text-white">{{ running_count }}</div>
    <div class="text-xs text-green-400 mt-1">↑ 较昨日 +{{ running_diff }}</div>
  </div>
  <div class="bg-app-card rounded-lg p-5 border border-gray-700">
    <div class="flex items-center justify-between mb-2">
      <span class="text-sm text-gray-400">异常风控数</span>
      <div class="w-9 h-9 rounded-lg flex items-center justify-center bg-red-500/15 text-red-400 text-lg">⚠️</div>
    </div>
    <div class="text-3xl font-bold text-white {{ 'text-red-400' if need_human_count > 0 }}">{{ need_human_count }}</div>
    <div class="text-xs mt-1">
      {% if need_human_count > 0 %}<span class="text-red-400 animate-pulse">⚡ 需立即处理</span>
      {% else %}<span class="text-gray-500">一切正常</span>{% endif %}
    </div>
  </div>
</div>
```
### 6.2 任务表格
```html
<!-- templates/dashboard.html -->
{% extends "base.html" %}
{% block content %}
<div class="flex justify-between items-center mb-6">
  <nav class="flex items-center gap-2 text-sm">
    <span class="text-gray-400">素材抓取</span><span class="text-gray-600">/</span>
    <span class="text-white font-medium">采集任务大厅</span>
  </nav>
  <button class="flex items-center gap-2 px-4 py-2 bg-brand hover:bg-brand-dark text-white rounded-lg text-sm font-medium"
          onclick="document.getElementById('create-modal').showModal()">
    <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>
    新建采集任务
  </button>
</div>
{% include "partials/_stat_cards.html" %}
<!-- 过滤栏 -->
<div class="flex gap-3 mb-5 items-center">
  <select class="h-9 px-3 bg-app-card border border-gray-700 rounded-md text-sm text-gray-300 focus:border-brand focus:outline-none">
    <option>Show 10</option><option>Show 20</option><option>Show 50</option>
  </select>
  <select class="h-9 px-3 bg-app-card border border-gray-700 rounded-md text-sm text-gray-300">
    <option value="">All Status</option><option value="running">运行中</option><option value="need_human">需人工</option><option value="completed">已完成</option>
  </select>
  <select class="h-9 px-3 bg-app-card border border-gray-700 rounded-md text-sm text-gray-300">
    <option value="">All Platform</option><option value="xhs">小红书</option><option value="zhihu">知乎</option>
  </select>
  <div class="relative flex-1 max-w-xs">
    <svg class="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="7" cy="7" r="5"/><path d="M15 15l-3-3"/></svg>
    <input type="text" placeholder="Search task..." class="w-full h-9 pl-9 pr-3 bg-app-card border border-gray-700 rounded-md text-sm text-gray-300 placeholder-gray-500 focus:border-brand focus:outline-none">
  </div>
</div>
<!-- 表格 -->
<div class="bg-app-card rounded-lg border border-gray-700 overflow-hidden">
  <table class="w-full">
    <thead>
      <tr class="bg-black/20 border-b border-gray-700">
        <th class="px-4 py-3"><input type="checkbox" id="select-all" class="rounded border-gray-600 bg-app-input text-brand"></th>
        <th class="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase">状态</th>
        <th class="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase">目标内容</th>
        <th class="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase">进度</th>
        <th class="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase">平台</th>
        <th class="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase">创建时间</th>
        <th class="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase">操作</th>
      </tr>
    </thead>
    <tbody>
      {% for task in tasks %}
      <tr id="task-row-{{ task.id }}" class="border-b border-gray-700/50 hover:bg-app-hover transition-colors">
        <td class="px-4 py-3"><input type="checkbox" class="row-check rounded border-gray-600 bg-app-input text-brand" value="{{ task.id }}"></td>
        <td class="px-4 py-3" id="status-{{ task.id }}" hx-get="/api/tasks/{{ task.id }}/status" hx-trigger="every 5s" hx-swap="innerHTML">
          {% include "partials/_status_badge.html" %}
        </td>
        <td class="px-4 py-3">
          <div class="font-medium text-white">{{ task.target_value[:30] }}</div>
          <div class="text-xs text-gray-500 mt-0.5">{{ task.task_type }} · {{ task.expected_count }}条</div>
          {% if task.error_msg %}<div class="text-xs text-red-400 mt-1">{{ task.error_msg }}</div>{% endif %}
        </td>
        <td class="px-4 py-3" id="progress-{{ task.id }}" hx-get="/api/tasks/{{ task.id }}/progress" hx-trigger="every 5s" hx-swap="innerHTML">
          {% include "partials/_progress_bar.html" %}
        </td>
        <td class="px-4 py-3">
          {% if task.platform == 'xhs' %}<span class="inline-block px-2.5 py-0.5 rounded text-xs font-medium bg-red-500/15 text-red-400">小红书</span>
          {% else %}<span class="inline-block px-2.5 py-0.5 rounded text-xs font-medium bg-blue-500/15 text-blue-400">知乎</span>{% endif %}
        </td>
        <td class="px-4 py-3 text-sm text-gray-400">{{ task.created_at.strftime('%Y-%m-%d %H:%M') }}</td>
        <td class="px-4 py-3" id="actions-{{ task.id }}" hx-get="/api/tasks/{{ task.id }}/actions" hx-trigger="every 5s" hx-swap="innerHTML">
          {% include "partials/_task_actions.html" %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  <!-- 分页 -->
  <div class="flex justify-between items-center px-6 py-4 border-t border-gray-700">
    <span class="text-sm text-gray-400">Showing {{ page_start }} to {{ page_end }} of {{ total }} tasks.</span>
    <div class="flex gap-1">
      <button {% if page <= 1 %}disabled{% endif %} hx-get="/?page={{ page - 1 }}" hx-target="main" class="min-w-[32px] h-8 px-2 border border-gray-700 rounded text-sm text-gray-300 hover:border-brand hover:text-brand disabled:opacity-40">&lt;</button>
      {% for p in range(1, total_pages + 1) %}
      <button class="min-w-[32px] h-8 px-2 rounded text-sm {% if p == page %}bg-brand text-white{% else %}border border-gray-700 text-gray-300 hover:border-brand hover:text-brand{% endif %}" hx-get="/?page={{ p }}" hx-target="main">{{ p }}</button>
      {% endfor %}
      <button {% if page >= total_pages %}disabled{% endif %} hx-get="/?page={{ page + 1 }}" hx-target="main" class="min-w-[32px] h-8 px-2 border border-gray-700 rounded text-sm text-gray-300 hover:border-brand hover:text-brand disabled:opacity-40">&gt;</button>
    </div>
  </div>
</div>
{% endblock %}
```
---
## 七、状态徽章 / 进度条 / 操作按钮 (Partials)
### 7.1 状态徽章
```html
<!-- templates/partials/_status_badge.html -->
{% set cfg = {
  'pending':    {'cls': 'bg-yellow-500/15 text-yellow-400',  'icon': '🟡', 'text': '排队中'},
  'running':    {'cls': 'bg-green-500/15 text-green-400',    'icon': '🟢', 'text': sub_status_text|default('运行中')},
  'paused':     {'cls': 'bg-gray-500/15 text-gray-400',      'icon': '⏸️', 'text': '已暂停'},
  'need_human': {'cls': 'bg-red-500/15 text-red-400',        'icon': '⚠️', 'text': '需人工处理'},
  'error':      {'cls': 'bg-purple-500/15 text-purple-400',  'icon': '⛔', 'text': '异常中止'},
  'completed':  {'cls': 'bg-green-500/15 text-green-400',    'icon': '✅', 'text': '已完成'}
}.get(task.status, {'cls': 'bg-gray-500/15 text-gray-400', 'icon': '🟡', 'text': '排队中'}) %}
<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium {{ cfg.cls }} {% if task.status == 'need_human' %}animate-pulse{% endif %}">
  <span>{{ cfg.icon }}</span><span>{{ cfg.text }}</span>
  {% if sub_stage_countdown %}<span class="text-[10px] opacity-70">({{ sub_stage_countdown }}s)</span>{% endif %}
</span>
```
### 7.2 进度条
```html
<!-- templates/partials/_progress_bar.html -->
{% set pct = (task.actual_count / task.expected_count * 100)|round if task.expected_count > 0 else 0 %}
<div class="flex items-center gap-2">
  <div class="flex-1 h-1.5 bg-gray-700 rounded-full overflow-hidden min-w-[80px]">
    <div class="h-full bg-gradient-to-r from-green-500 to-emerald-400 rounded-full transition-all duration-500" style="width: {{ pct }}%"></div>
  </div>
  <span class="text-xs text-gray-400 whitespace-nowrap">{{ task.actual_count }}/{{ task.expected_count }}</span>
</div>
```
### 7.3 动态操作按钮
```html
<!-- templates/partials/_task_actions.html -->
<div class="flex items-center gap-2">
  {% if task.status == 'running' %}
    <button class="text-gray-400 hover:text-yellow-400" title="暂停" hx-post="/api/tasks/{{ task.id }}/pause" hx-swap="none">
      <svg width="16" height="16" fill="currentColor"><rect x="4" y="3" width="3" height="10"/><rect x="11" y="3" width="3" height="10"/></svg>
    </button>
  {% elif task.status == 'paused' %}
    <button class="text-green-400 hover:text-green-300" title="恢复" hx-post="/api/tasks/{{ task.id }}/resume" hx-swap="none">
      <svg width="16" height="16" fill="currentColor"><path d="M5 3l9 5-9 5V3z"/></svg>
    </button>
  {% elif task.status == 'need_human' %}
    <button class="px-3 py-1 bg-red-500/15 text-red-400 border border-red-500/30 rounded text-xs font-medium hover:bg-red-500/25" onclick="activateBrowser('{{ task.id }}')">🖥️ 唤起浏览器</button>
    <button class="px-3 py-1 bg-green-500/15 text-green-400 border border-green-500/30 rounded text-xs font-medium hover:bg-green-500/25" hx-post="/api/tasks/{{ task.id }}/resume" hx-swap="none">✅ 已处理</button>
  {% elif task.status == 'completed' %}
    <a href="/data?task_id={{ task.id }}" class="text-gray-400 hover:text-blue-400" title="查看">
      <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
    </a>
    <a href="/api/tasks/{{ task.id }}/export" class="text-gray-400 hover:text-blue-400" title="导出">
      <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2"/></svg>
    </a>
  {% elif task.status == 'error' %}
    <button class="text-gray-400 hover:text-blue-400" title="重试" hx-post="/api/tasks/{{ task.id }}/retry" hx-swap="none">
      <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 4v6h6M3.51 15a9 9 0 102.13-9.36L1 10"/></svg>
    </button>
    <button class="text-gray-400 hover:text-red-400" title="删除" hx-delete="/api/tasks/{{ task.id }}" hx-target="#task-row-{{ task.id }}" hx-swap="outerHTML">
      <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/></svg>
    </button>
  {% endif %}
</div>
```
---
## 八、创建任务弹窗 + 耗时预估
```html
<!-- templates/partials/_create_task_modal.html -->
<dialog id="create-modal" class="bg-transparent p-0 max-w-[520px] w-[90%]">
  <div class="bg-app-card rounded-xl border border-gray-700 shadow-2xl flex flex-col">
    <div class="flex justify-between items-center px-6 py-4 border-b border-gray-700">
      <h3 class="text-lg font-semibold text-white">新建采集任务</h3>
      <button class="w-7 h-7 flex items-center justify-center rounded text-gray-400 hover:bg-gray-700" onclick="document.getElementById('create-modal').close()">✕</button>
    </div>
    <form id="create-task-form" class="p-6 space-y-5" hx-post="/api/tasks" hx-target="#task-list-body" hx-swap="afterbegin" hx-on::after-request="if(event.detail.successful){document.getElementById('create-modal').close();}">
      <div>
        <label class="block text-sm font-medium text-gray-300 mb-2">采集平台 <span class="text-red-400">*</span></label>
        <div class="flex gap-3">
          <label class="radio-card selected flex-1 flex flex-col items-center gap-1.5 p-4 border-2 border-gray-600 rounded-lg cursor-pointer">
            <input type="radio" name="platform" value="xhs" checked class="hidden"><span class="text-2xl">📱</span><span class="text-sm font-medium">小红书</span>
          </label>
          <label class="radio-card flex-1 flex flex-col items-center gap-1.5 p-4 border-2 border-gray-600 rounded-lg cursor-pointer">
            <input type="radio" name="platform" value="zhihu" class="hidden"><span class="text-2xl">📘</span><span class="text-sm font-medium">知乎</span>
          </label>
        </div>
      </div>
      <div>
        <label class="block text-sm font-medium text-gray-300 mb-2">任务类型 <span class="text-red-400">*</span></label>
        <select name="task_type" id="task-type-select" class="w-full h-10 px-3 bg-app-input border border-gray-600 rounded-lg text-sm text-gray-200 focus:border-brand focus:outline-none" onchange="toggleTaskTypeFields()">
          <option value="keyword">关键词搜索</option><option value="url">指定 URL</option>
        </select>
      </div>
      <div id="keyword-field">
        <label class="block text-sm font-medium text-gray-300 mb-2">搜索关键词 <span class="text-red-400">*</span></label>
        <input type="text" name="keyword" class="w-full h-10 px-3 bg-app-input border border-gray-600 rounded-lg text-sm text-gray-200 placeholder-gray-500 focus:border-brand focus:outline-none" placeholder="输入搜索词，如: AI编程" maxlength="20">
        <small class="block text-xs text-gray-500 mt-1.5">1-20 个字符</small>
      </div>
      <div id="url-field" class="hidden">
        <label class="block text-sm font-medium text-gray-300 mb-2">目标 URL <span class="text-red-400">*</span></label>
        <textarea name="urls" rows="4" class="w-full p-3 bg-app-input border border-gray-600 rounded-lg text-sm text-gray-200 placeholder-gray-500 focus:border-brand focus:outline-none resize-y" placeholder="每行一个链接"></textarea>
        <small class="block text-xs text-gray-500 mt-1.5">最多 10 个 URL</small>
      </div>
      <div>
        <label class="block text-sm font-medium text-gray-300 mb-2">采集数量 <span class="text-red-400">*</span></label>
        <div class="flex items-center gap-2">
          <button type="button" class="count-btn h-8 px-4 border border-gray-600 rounded-lg text-sm bg-app-card text-gray-300" data-count="10">10</button>
          <button type="button" class="count-btn active h-8 px-4 rounded-lg text-sm bg-brand text-white border border-brand" data-count="50">50</button>
          <button type="button" class="count-btn h-8 px-4 border border-gray-600 rounded-lg text-sm bg-app-card text-gray-300" data-count="100">100</button>
          <span class="text-gray-500 text-sm mx-1">或自定义:</span>
          <input type="number" name="expected_count" value="50" min="1" max="200" class="w-16 h-8 px-2 bg-app-input border border-gray-600 rounded-lg text-sm text-gray-200 focus:border-brand focus:outline-none">
          <span class="text-gray-500 text-sm">条</span>
        </div>
        <small class="block text-xs text-gray-500 mt-1.5">单任务上限 200 条</small>
      </div>
    </form>
    <div class="flex justify-end gap-3 px-6 py-4 border-t border-gray-700">
      <button class="px-4 py-2 bg-app-card border border-gray-600 rounded-lg text-sm text-gray-200 hover:bg-gray-700" onclick="document.getElementById('create-modal').close()">取消</button>
      <button class="px-4 py-2 bg-brand hover:bg-brand-dark text-white rounded-lg text-sm font-medium" onclick="submitWithEstimate()">开始采集 →</button>
    </div>
  </div>
</dialog>
<dialog id="estimate-modal" class="bg-transparent p-0 max-w-[400px] w-[90%]">
  <div class="bg-app-card rounded-xl border border-gray-700 shadow-2xl flex flex-col">
    <div class="px-6 py-4 border-b border-gray-700"><h3 class="text-lg font-semibold text-white">⏳ 预期耗时提醒</h3></div>
    <div class="p-6 text-center">
      <div class="text-4xl mb-4">📊</div>
      <p class="text-gray-300 mb-2">采用拟人防封机制，本次任务预计需要 <strong id="estimate-minutes" class="text-brand text-lg">60-90</strong> 分钟。</p>
      <p class="text-sm text-gray-500 mt-2">任务在后台缓慢运行，期间可关闭此窗口。</p>
    </div>
    <div class="flex justify-end gap-3 px-6 py-4 border-t border-gray-700">
      <button class="px-4 py-2 bg-app-card border border-gray-600 rounded-lg text-sm text-gray-200" onclick="document.getElementById('estimate-modal').close()">取消</button>
      <button class="px-4 py-2 bg-brand hover:bg-brand-dark text-white rounded-lg text-sm font-medium" onclick="confirmCreate()">✅ 确认挂机</button>
    </div>
  </div>
</dialog>
```
---
## 九、风控接管页 (新增 - 左右分栏全屏)
```html
<!-- templates/partials/_risk_control_modal.html -->
<dialog id="risk-control-modal" class="bg-transparent p-0 max-w-[800px] w-[90%]">
  <div class="flex rounded-xl overflow-hidden shadow-2xl border border-gray-700" style="min-height: 400px;">
    <!-- 左侧深色操作区 -->
    <div class="flex-1 bg-app-card p-8 flex flex-col justify-center">
      <div class="text-red-400 text-sm font-medium mb-2 flex items-center gap-2">
        <span class="w-2 h-2 rounded-full bg-red-500 animate-pulse"></span>安全接管模式已激活
      </div>
      <h2 class="text-2xl font-bold text-white mb-4">系统检测到平台风控拦截</h2>
      <p class="text-gray-400 mb-6 leading-relaxed">
        任务 <span class="text-white font-medium">{{ task.target_value[:20] }}</span> 在采集过程中触发了验证机制。<br>
        请在弹出的浏览器中手动完成验证，完成后点击下方按钮继续。
      </p>
      <div class="space-y-3">
        <button class="w-full py-3 bg-brand hover:bg-brand-dark text-white rounded-lg font-medium flex items-center justify-center gap-2" onclick="activateBrowser('{{ task.id }}')">
          <svg width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>
          唤起 Chrome 浏览器
        </button>
        <button class="w-full py-3 bg-green-500/15 text-green-400 border border-green-500/30 rounded-lg font-medium hover:bg-green-500/25" hx-post="/api/tasks/{{ task.id }}/resume" hx-swap="none" onclick="document.getElementById('risk-control-modal').close()">
          ✅ 我已处理，继续任务
        </button>
      </div>
    </div>
    <!-- 右侧暖橙渐变警告区 -->
    <div class="w-[280px] flex flex-col items-center justify-center p-8" style="background: linear-gradient(135deg, #FF6B35 0%, #FF4560 100%);">
      <div class="text-6xl mb-4">🛡️</div>
      <h3 class="text-white text-xl font-bold mb-3 text-center">安全接管模式</h3>
      <p class="text-white/80 text-sm text-center leading-relaxed">人工验证不会泄露账号信息<br>验证完成后系统将自动恢复采集</p>
      <div class="mt-6 px-4 py-2 bg-white/15 rounded-lg backdrop-blur-sm"><span class="text-white/90 text-xs">任务 ID: {{ task.id[:8] }}</span></div>
    </div>
  </div>
</dialog>
```
---
## 十、数据预览面板
```html
<!-- templates/data_viewer.html -->
{% extends "base.html" %}
{% block content %}
<div class="flex justify-between items-center mb-6">
  <nav class="flex items-center gap-2 text-sm">
    <a href="/" class="text-gray-400 hover:text-brand">素材加工</a><span class="text-gray-600">/</span>
    <span class="text-gray-400">数据资产库</span><span class="text-gray-600">/</span>
    <span class="text-white font-medium">{{ task.target_value[:20] }}</span>
  </nav>
  <a href="/api/tasks/{{ task.id }}/export" class="flex items-center gap-2 px-4 py-2 bg-brand hover:bg-brand-dark text-white rounded-lg text-sm font-medium">
    <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2"/></svg>导出 CSV
  </a>
</div>
<!-- 元信息卡片 -->
<div class="flex gap-8 bg-app-card rounded-lg border border-gray-700 p-5 mb-5">
  <div class="flex flex-col gap-1"><span class="text-xs text-gray-500">平台</span><span class="text-sm font-medium text-white">{{ '小红书' if task.platform == 'xhs' else '知乎' }}</span></div>
  <div class="flex flex-col gap-1"><span class="text-xs text-gray-500">关键词</span><span class="text-sm font-medium text-white">{{ task.target_value }}</span></div>
  <div class="flex flex-col gap-1"><span class="text-xs text-gray-500">采集数</span><span class="text-sm font-medium text-white">{{ task.actual_count }} 篇</span></div>
  <div class="flex flex-col gap-1"><span class="text-xs text-gray-500">评论数</span><span class="text-sm font-medium text-white">{{ total_comments }} 条</span></div>
</div>
<!-- 数据表格 -->
<div class="bg-app-card rounded-lg border border-gray-700 overflow-hidden">
  <table class="w-full">
    <thead>
      <tr class="bg-black/20 border-b border-gray-700">
        <th class="w-8 px-4 py-3"></th>
        <th class="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase">标题</th>
        <th class="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase">作者</th>
        <th class="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase cursor-pointer hover:text-brand">点赞 ↓</th>
        <th class="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase">评论数</th>
        <th class="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase">发布时间</th>
      </tr>
    </thead>
    <tbody id="items-table-body">
      {% for item in items %}
      <tr class="border-b border-gray-700/50 hover:bg-app-hover transition-colors cursor-pointer" onclick="toggleExpand('{{ item.id }}')">
        <td class="px-4 py-3"><svg class="expand-icon transition-transform" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" id="icon-{{ item.id }}"><path d="M4 6l4 4 4-4"/></svg></td>
        <td class="px-4 py-3">
          <a href="{{ item.source_url }}" target="_blank" class="text-white font-medium hover:text-brand">{{ item.title or '(无标题)' }}</a>
          <div class="text-xs text-gray-500 mt-0.5">{{ item.content[:60] }}...</div>
        </td>
        <td class="px-4 py-3 text-gray-300">{{ item.author or '未知' }}</td>
        <td class="px-4 py-3"><span class="inline-flex items-center gap-1.5 text-gray-300"><span class="w-1.5 h-1.5 rounded-full bg-green-500"></span>{{ item.likes }}</span></td>
        <td class="px-4 py-3 text-gray-300">{{ item.comments_count }}</td>
        <td class="px-4 py-3 text-sm text-gray-400">{{ item.publish_time or '-' }}</td>
      </tr>
      <tr class="hidden" id="comments-row-{{ item.id }}"><td colspan="6" class="px-4"><div class="bg-black/20 rounded-lg m-2 p-4" id="comments-{{ item.id }}"></div></td></tr>
      {% endfor %}
    </tbody>
  </table>
  <div class="px-6 py-4 border-t border-gray-700"><span class="text-sm text-gray-400">共 {{ items|length }} 篇笔记</span></div>
</div>
{% endblock %}
```
### 评论子表格
```html
<!-- templates/partials/_comments.html -->
<div>
  <div class="text-sm text-gray-400 mb-3">💬 Top {{ comments|length }} 条热门评论</div>
  <table class="w-full">
    <thead><tr class="border-b border-gray-700">
      <th class="px-3 py-2 text-left text-xs text-gray-500">评论者</th>
      <th class="px-3 py-2 text-left text-xs text-gray-500">评论内容</th>
      <th class="px-3 py-2 text-right text-xs text-gray-500">点赞</th>
    </tr></thead>
    <tbody>
      {% for c in comments %}
      <tr class="border-b border-gray-700/30">
        <td class="px-3 py-2.5 text-sm font-medium text-blue-400 whitespace-nowrap">{{ c.author }}</td>
        <td class="px-3 py-2.5 text-sm text-gray-300">{{ c.content }}</td>
        <td class="px-3 py-2.5 text-sm text-gray-500 text-right">{{ c.likes }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
```
---
## 十一、全局 JS (app.js)
```javascript
/* static/js/app.js */
function showToast(msg, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  const bg = { warning: '#FEB019', error: '#FF4560', success: '#00E396', info: '#2A303D' };
  toast.style.cssText = `background:${bg[type]||bg.info};color:#fff;padding:10px 20px;border-radius:8px;font-size:13px;box-shadow:0 4px 12px rgba(0,0,0,0.3);animation:slideIn 0.3s;border:1px solid rgba(255,255,255,0.1);`;
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(() => { toast.style.opacity = '0'; toast.style.transition = 'opacity 0.3s'; setTimeout(() => toast.remove(), 300); }, 3000);
}
document.querySelectorAll('.radio-card').forEach(card => {
  card.addEventListener('click', () => {
    card.parentElement.querySelectorAll('.radio-card').forEach(c => c.classList.remove('selected'));
    card.classList.add('selected');
    card.querySelector('input[type="radio"]').checked = true;
  });
});
function toggleTaskTypeFields() {
  const type = document.getElementById('task-type-select').value;
  document.getElementById('keyword-field').classList.toggle('hidden', type === 'url');
  document.getElementById('url-field').classList.toggle('hidden', type === 'keyword');
}
document.querySelectorAll('.count-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.count-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.querySelector('[name="expected_count"]').value = btn.dataset.count;
  });
});
function submitWithEstimate() {
  const form = document.getElementById('create-task-form');
  const count = parseInt(form.querySelector('[name="expected_count"]').value);
  if (count > 200) { form.querySelector('[name="expected_count"]').value = 200; showToast('单任务上限 200 条，已自动调整', 'warning'); return; }
  if (count < 1) { showToast('数量不能小于 1', 'error'); return; }
  const groups = Math.floor(count / 5);
  const minMin = Math.round(count * 1.0 + groups * 1.0);
  const maxMin = Math.round(count * 1.5 + groups * 1.5);
  document.getElementById('estimate-minutes').textContent = `${minMin}-${maxMin}`;
  document.getElementById('estimate-modal').showModal();
}
function confirmCreate() {
  document.getElementById('estimate-modal').close();
  htmx.trigger('#create-task-form', 'submit');
}
function activateBrowser(taskId) {
  fetch(`/api/tasks/${taskId}/activate-browser`, { method: 'POST' })
    .then(res => res.json())
    .then(data => { if (data.success) showToast('已唤起 Chrome，请手动处理验证码'); else showToast('唤起失败: ' + data.error, 'error'); })
    .catch(() => showToast('网络错误', 'error'));
}
function toggleExpand(itemId) {
  const row = document.getElementById('comments-row-' + itemId);
  const icon = document.getElementById('icon-' + itemId);
  if (!row || !icon) return;
  if (row.classList.contains('hidden')) {
    row.classList.remove('hidden');
    icon.style.transform = 'rotate(180deg)';
    const container = document.getElementById('comments-' + itemId);
    if (container && !container.dataset.loaded) {
      htmx.ajax('GET', `/api/items/${itemId}/comments`, { target: '#comments-' + itemId, swap: 'innerHTML' });
      container.dataset.loaded = '1';
    }
  } else {
    row.classList.add('hidden');
    icon.style.transform = 'rotate(0deg)';
  }
}
document.getElementById('select-all')?.addEventListener('change', function() {
  document.querySelectorAll('.row-check').forEach(cb => cb.checked = this.checked);
});
```
---
## 十二、文件目录结构
```
项目根目录/
├── templates/
│   ├── base.html                        # 页面骨架 (含顶栏 + Tailwind配置 + 暗色滚动条)
│   ├── dashboard.html                    # 采集任务大厅
│   ├── data_viewer.html                  # 数据资产库
│   └── partials/
│       ├── _sidebar.html                 # 侧边栏 (四大业务板块)
│       ├── _stat_cards.html              # 三色统计卡片 (新增)
│       ├── _create_task_modal.html       # 创建任务弹窗 + 耗时预估弹窗
│       ├── _risk_control_modal.html      # 风控接管页 (左右分栏全屏 - 新增)
│       ├── _status_badge.html            # 状态徽章组件
│       ├── _progress_bar.html            # 进度条组件
│       ├── _task_actions.html            # 动态操作按钮组
│       └── _comments.html               # 评论子表格
│
├── static/
│   └── js/
│       └── app.js                        # 全局交互脚本
│
└── （Tailwind CSS 通过 CDN 引入，无需本地 CSS 文件）
```
---
## 十三、平台登录配置页 (`/accounts`)
> 侧边栏「素材抓取 → 平台登录配置」入口，对应路由 `GET /accounts`（`active_page: "auth"`）。这是采集的全流程起点：任何采集任务必须挂在一个已登录的账号壳上。本节定义账号管理的**数据模型**、**交互流程**、**管理操作**。

### 13.1 概念模型（三个标识的区分）
| 标识 | 含义 | 来源 |
|---|---|---|
| `account_id` | 系统内部自增主键 | 添加账号时系统生成 |
| `platform_user_id` | 平台侧真实用户ID（小红书 user_id 等） | 从 cookie / 扫码成功后提取 |
| `remark`（账号备注） | 用户手填的备注标签，**必填** | 添加账号表单 |
| `platform_nickname` | 平台显示的真实昵称 | 导入/登录时自动提取，不可手改 |
| `platform_id` | 采集到的笔记/评论平台ID，用于去重 | 与账号无关 |

> **账号备注与平台昵称是两个用途**：前者是用户自己认账号的别名（"我的小号A"），后者是平台显示的真实昵称。列表展示 `备注 (平台昵称)` 最清楚。账号备注 NOT NULL；`platform_user_id` 通过 `UNIQUE(platform, platform_user_id)` 去重，防止重复导入同一 cookie 建出重复账号。

### 13.2 数据模型（`accounts` 表增强）
| 字段 | 类型 | 说明 | 处置 |
|---|---|---|---|
| `id` | Integer PK | 内部自增主键 | 保留 |
| `platform` | String(20) | 平台名 | 保留 |
| `remark` | String(100) NOT NULL | 账号备注（原 `nickname` 改名+加约束） | **改名+NOT NULL** |
| `platform_user_id` | String(64) nullable | 平台真实用户ID | **新增** |
| `platform_nickname` | String(100) nullable | 平台真实昵称 | **新增** |
| `login_method` | String(20) | `qrcode` / `cookie_import` | 保留 |
| `color_scheme`/`timezone`/`locale`/`viewport_w`/`viewport_h` | — | 一账号一固定指纹（spec §4.1+§7.2 反检测设计） | 保留 |
| `status` | String(20) | `inactive`/`active`/`suspended`/`banned` | 保留 |
| `last_login_at`/`last_scrape_at` | DateTime | 最后登录/采集时间 | 保留 |
| `fail_count` | Integer | 连续失败计数，成功清零，达 5 → suspended | 保留 |
| `daily_scrape_count`/`total_scrape_count` | Integer | 计数（注：S8 已改用 SQLite COUNT 当日入库） | 保留 |
| `notes`/时间戳 | — | 备注与时间戳 | 保留 |
| ~~`phone`~~ | — | 全代码无引用 | **删** |
| ~~`profile_dir`~~ | — | 用 `profile_dir_for(account_id)` 动态算，不入库 | **删** |

**约束变更**：
- 新增 `UNIQUE(platform, platform_user_id)`（`platform_user_id` 为空时不参与唯一约束，允许未登录的空壳并存）
- `remark` NOT NULL

**关系**：
```
accounts (1) ──FK──→ (N) collection_tasks   [task.account_id 补 ForeignKey，采集任务明确绑定账号]
```
> PRD §6 契约曾写"collection_tasks 无 account_id"，但采集必须知道用哪个账号登录态抓的 → 裁决**补 FK 对齐现状**，PRD 那条是规格漂移。

**Cookie 存储（文件，非 DB）**：`data/collection/profiles/{account_id}/cookies.json`，Playwright `add_cookies` 接受的 `[{name,value,domain,path,...}]`。与 Chrome profile 同目录（Chrome 用 `--user-data-dir=profiles/{account_id}/`）。worker 启动时若 Chrome profile 无登录态则从 `cookies.json` `add_cookies` 注入。

### 13.3 页面布局
三个区块自上而下：**添加账号** / **导入 Cookie** / **账号列表**。全程 HTMX 局部替换，无整页 reload。

```html
<!-- templates/accounts.html -->
{% extends "base.html" %}
{% block title %}平台登录配置{% endblock %}
{% block content %}
<!-- 区块一：添加账号（建空壳） -->
<div class="bg-app-card rounded-lg border border-gray-700 p-6 mb-6">
  <h3 class="text-lg font-semibold text-white mb-4">添加账号</h3>
  <form hx-post="/api/accounts" hx-target="#accounts-list" hx-swap="outerHTML">
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
      <div>
        <label class="block text-sm text-gray-300 mb-2">平台 <span class="text-red-400">*</span></label>
        <select name="platform" class="w-full h-10 px-3 bg-app-input border border-gray-600 rounded-lg text-sm text-gray-200">
          {% for p in platforms %}<option value="{{ p }}">{{ p }}</option>{% endfor %}
        </select>
      </div>
      <div>
        <label class="block text-sm text-gray-300 mb-2">账号备注 <span class="text-red-400">*</span></label>
        <input type="text" name="remark" required placeholder="如：小号A" class="w-full h-10 px-3 bg-app-input border border-gray-600 rounded-lg text-sm text-gray-200">
      </div>
    </div>
    <button type="submit" class="px-4 py-2 bg-brand hover:bg-brand-dark text-white rounded-lg text-sm font-medium">添加账号</button>
  </form>
</div>

<!-- 区块二：导入 Cookie（快捷登录） -->
<div class="bg-app-card rounded-lg border border-gray-700 p-6 mb-6">
  <h3 class="text-lg font-semibold text-white mb-4">导入 Cookie</h3>
  <form hx-post="/api/accounts/import-cookies" hx-target="#accounts-list" hx-swap="outerHTML">
    <div class="space-y-4 mb-4">
      <div>
        <label class="block text-sm text-gray-300 mb-2">选择账号 <span class="text-red-400">*</span></label>
        <select name="account_id" required class="w-full h-10 px-3 bg-app-input border border-gray-600 rounded-lg text-sm text-gray-200">
          <option value="">请选择已添加的账号壳</option>
          {% for a in accounts if a.status == 'inactive' %}
          <option value="{{ a.id }}">{{ a.remark }}（#{{ a.id }} · {{ a.platform }}）</option>
          {% endfor %}
        </select>
      </div>
      <div>
        <label class="block text-sm text-gray-300 mb-2">Cookie JSON <span class="text-red-400">*</span></label>
        <textarea name="cookies" rows="3" required placeholder='[{"name":"...","value":"...","domain":"..."}]' class="w-full p-3 bg-app-input border border-gray-600 rounded-lg text-sm text-gray-200 resize-y"></textarea>
      </div>
    </div>
    <button type="submit" class="px-4 py-2 bg-brand hover:bg-brand-dark text-white rounded-lg text-sm font-medium">导入并验证</button>
  </form>
</div>

<!-- 区块三：账号列表 -->
<div id="accounts-list" class="bg-app-card rounded-lg border border-gray-700 overflow-hidden">
  <div class="px-6 py-4 border-b border-gray-700"><h3 class="text-lg font-semibold text-white">账号列表 ({{ accounts|length }})</h3></div>
  <table class="w-full">
    <thead><tr class="bg-black/20 border-b border-gray-700">
      <th class="px-4 py-3 text-left text-xs text-gray-400 uppercase">备注 (平台昵称)</th>
      <th class="px-4 py-3 text-left text-xs text-gray-400 uppercase">平台</th>
      <th class="px-4 py-3 text-left text-xs text-gray-400 uppercase">状态</th>
      <th class="px-4 py-3 text-left text-xs text-gray-400 uppercase">最后登录</th>
      <th class="px-4 py-3 text-left text-xs text-gray-400 uppercase">操作</th>
    </tr></thead>
    <tbody>
      {% for a in accounts %}
      <tr class="border-b border-gray-700/50 hover:bg-app-hover">
        <td class="px-4 py-3 text-sm text-white">{{ a.remark }}
          {% if a.platform_nickname %}<span class="text-xs text-gray-500 ml-1">({{ a.platform_nickname }})</span>{% endif %}
        </td>
        <td class="px-4 py-3">{% include "partials/_platform_badge.html" %}</td>
        <td class="px-4 py-3">{% include "partials/_account_status_badge.html" %}</td>
        <td class="px-4 py-3 text-sm text-gray-400">{{ a.last_login_at.strftime('%Y-%m-%d %H:%M') if a.last_login_at else '—' }}</td>
        <td class="px-4 py-3">
          <button hx-post="/api/accounts/{{ a.id }}/login" hx-swap="none" class="px-3 py-1 bg-brand/15 text-brand border border-brand/30 rounded text-xs">登录</button>
          <button hx-post="/api/accounts/{{ a.id }}/validate" hx-swap="none" class="px-3 py-1 bg-green-500/15 text-green-400 border border-green-500/30 rounded text-xs">验证</button>
          <button hx-get="/api/accounts/{{ a.id }}/edit" class="px-3 py-1 bg-gray-500/15 text-gray-300 border border-gray-500/30 rounded text-xs">编辑备注</button>
          <button hx-delete="/api/accounts/{{ a.id }}" hx-confirm="确定删除？将同时清除该账号的浏览器配置与Cookie。" class="px-3 py-1 bg-red-500/15 text-red-400 border border-red-500/30 rounded text-xs">删除</button>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

### 13.4 交互流程
#### 流程 A — 添加账号壳
1. 用户填 **平台**（下拉）+ **账号备注**（必填）→【添加账号】
2. 系统：建 `Account` 行（`status=inactive`、`platform_user_id` 为空），返回 `id`
3. 列表出现一行灰「未激活」——**此时只是空壳**，无登录态、无 cookie、无平台真实身份

#### 流程 B — 扫码登录（路径 1）
1. 用户在某行点【登录】→ `POST /api/accounts/{id}/login`
2. 系统：worker 拉起 Chrome → 导航 `login_url` → 截图 QR → WS 广播 `qr_ready`（含截图）→ 前端弹二维码
3. 用户：手机扫码
4. 系统：轮询 `success_pattern`（≤120s）→ 成功后 `context.cookies()` 提取 → 落 `cookies.json` + `add_cookies` 注入 → 解析 `platform_user_id`/`platform_nickname` 回写 → `status=active` + `last_login_at`
5. WS 广播 `login_success` → 列表徽章转绿 + 平台昵称自动填出

#### 流程 C — 导入 Cookie（路径 2，快捷）
1. 用户在「导入 Cookie」卡片：**选账号**（下拉选已有空壳，非手填数字）+ **cookie JSON**
2. 点【导入并验证】→ `POST /api/accounts/import-cookies`
3. 系统：`json.loads` → `add_cookies` 注入 → 用注入的 cookie 请求平台需登录接口验证：
   - **验证成功** → 提取 `platform_user_id`/`platform_nickname` 回写 → `status=active` + `last_login_at`
   - **cookie 无效** → 留 `inactive` + `fail_count+1` + Toast 报错
   - **`platform_user_id` 命中同平台已有账号** → **拒绝**，提示「该平台账号已存在为账号 #N，是否更新它的 cookie」（用户裁决：选 A 拒绝，不静默合并）

### 13.5 管理操作
| 动作 | 触发 | 系统行为 |
|---|---|---|
| 状态徽章 | 列表 5s 轮询 | active 绿脉冲 / inactive 灰 / suspended 红 / banned 深红 |
| 验证登录态 | 点【验证】 | 加载 cookie → 请求需登录接口 → 刷新 status + fail_count（成功清零，连续 5 次 suspended） |
| 编辑备注 | 点【编辑备注】→ dialog | `PUT /api/accounts/{id}`，只改 `remark`，不改 `platform_user_id`/`platform_nickname` |
| 失效检测 | 采集中风控探针命中 URL 跳 `/login` | 任务 `need_human` + 账号 `fail_count+1` + UI 提示唤起扫码 |
| 重新登录 | need_human 唤起 | 回到流程 B |
| 删除 | 点【删除】+ 确认 | 清 profile 目录 + cookies.json + DB 行；**若该账号有 running 任务则拒绝删除（先停任务）** |
| 状态不可手改 | — | active/inactive/suspended/banned 由系统按验证结果/失败计数驱动，用户不能直接切 |
| 平台身份不可手改 | — | `platform_user_id`/`platform_nickname` 只能由系统从平台提取 |

### 13.6 账号状态机
```
inactive ──登录成功/cookie验证通过──→ active
   ↑                                   │
   │                              失效检测/验证失败×5
   │                                   ↓
   └────────────────── suspended（连续失败达 5 次）
```
- 成功（扫码或验证）→ 清零 `fail_count` → `active`
- 验证失败 → `fail_count+1`，达 5 → `suspended`
- 无静默续期（PRD 不重试 Login/SessionExpired 异常红线）
---
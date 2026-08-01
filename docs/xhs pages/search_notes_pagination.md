# 小红书搜索笔记翻页 API（真实抓包 2026-07-31）

## 这是真正的笔记列表接口（滚动翻页时触发）

### Request
- URL: `https://so.xiaohongshu.com/api/sns/web/v2/search/notes`
- Method: POST
- Content-Type: `application/json;charset=UTF-8`
- Status: 200 OK
- Domain: `so.xiaohongshu.com`（注意：不是 edith 也不是 www）

### Key Headers
- origin: https://www.xiaohongshu.com
- referer: https://www.xiaohongshu.com/
- x-s: (动态签名)
- x-s-common: (通用签名)
- x-t: (时间戳，如 1785515072924)
- x-b3-traceid: (追踪ID)
- x-rap-param: (加密参数，很长)

### 行为观察
- 首屏44条笔记通过 SSR 直出在 DOM 中，**不触发此 XHR**
- 向下滚动时触发此 POST 请求获取下一页笔记
- 同时伴随大量 `t2.xiaohongshu.com/api/v2/collect` POST 请求（埋点/行为上报）

### 关键结论
1. 首页数据来源 = SSR DOM（无 XHR），url_pattern 拦截对首页无效
2. 翻页数据来源 = 此 POST 请求，url_pattern "search/notes" 可命中
3. API 已升级到 v2（之前配置假设 v1）
4. 域名从 edith.xiaohongshu.com 变更为 so.xiaohongshu.com
5. Response body 待补充（用户尚未提供）

### collect 埋点接口（仅记录，不采集）
- URL: `https://t2.xiaohongshu.com/api/v2/collect`
- Method: POST
- Response: `{"code":0,"msg":"Success","success":true}`
- 说明：滚动时高频触发的行为埋点，与笔记数据无关

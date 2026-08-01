# 小红书搜索联想词 API

## 注意：这是搜索建议/联想词接口，不是笔记列表接口

### Request
- URL: `https://edith.xiaohongshu.com/api/sns/web/v1/search/recommend?keyword=AI+coding`
- Method: GET
- Status: 200 OK

### Key Headers
- origin: https://www.xiaohongshu.com
- referer: https://www.xiaohongshu.com/
- x-s: (签名，动态生成)
- x-t: (时间戳)
- x-s-common: (通用签名)

### Response
见 search_recommend_response.json

### 结论
此接口返回搜索建议词列表（sug_items），不包含笔记内容。
真正的笔记数据来源：SSR 首屏直出（__INITIAL_STATE__ 内嵌于 HTML）+ 页面 DOM 渲染。
用户在 console 运行 `window.__INITIAL_STATE__` 返回空，推测 Vue hydrate 后被清除。

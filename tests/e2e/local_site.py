"""A real local website for end-to-end tests.

This is not a mock of our own code: it is an actual HTTP server serving
actual HTML that issues actual XHR requests, so the browser worker, the
step-chain replay, the XHR interception and the JSONPath extraction all
run for real against it.

Pages
    /login                  login page; flips to / after `login_delay_ms`
                            (stands in for "user scanned the QR code")
    /search?keyword=&sort=  search page; fetches /api/search
    /explore/<item_id>      detail page; fetches /api/feed
                            comments load only after a scroll, like the
                            real platforms do
APIs
    /api/search             {data: {items: [...]}}
    /api/feed               {note: {...}}
    /api/comments           {data: {comments: [...]}}
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SEARCH_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>search {keyword}</title></head>
<body>
  <h1>搜索：{keyword}（{sort}）</h1>
  <div id="results">loading…</div>
  <script>
    fetch('/api/search?keyword={keyword}&sort={sort}', {{method: 'POST'}})
      .then(function (r) {{ return r.json(); }})
      .then(function (d) {{
        var box = document.getElementById('results');
        box.innerHTML = d.data.items.map(function (it) {{
          return '<a class="note" href="/explore/' + it.note_id + '">' +
                 it.display_title + '</a>';
        }}).join('');
      }});
  </script>
</body></html>
"""

DETAIL_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>note {item_id}</title></head>
<body>
  <article id="note">loading…</article>
  <div id="comments">滚动加载评论</div>
  <script>
    var itemId = '{item_id}';
    fetch('/api/feed?id=' + itemId, {{method: 'POST'}})
      .then(function (r) {{ return r.json(); }})
      .then(function (d) {{
        document.getElementById('note').innerHTML =
          '<h1 class="title">' + d.note.title + '</h1>' +
          '<p class="body">' + d.note.desc + '</p>';
      }});

    // Comments are lazy: they only load once the reader scrolls.
    var loaded = false;
    function loadComments() {{
      if (loaded) return;
      loaded = true;
      fetch('/api/comments?id=' + itemId)
        .then(function (r) {{ return r.json(); }})
        .then(function (d) {{
          document.getElementById('comments').innerHTML =
            d.data.comments.map(function (c) {{
              return '<p class="comment">' + c.content + '</p>';
            }}).join('');
        }});
    }}
    window.addEventListener('scroll', loadComments);
    document.body.style.minHeight = '3000px';
  </script>
</body></html>
"""

LOGIN_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>login</title></head>
<body>
  <h1 id="qr">扫码登录</h1>
  <img id="qr-code" alt="qr" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
  <script>
    // Stands in for the user scanning: the site redirects home when done.
    setTimeout(function () {{ window.location.href = '/'; }}, {login_delay_ms});
  </script>
</body></html>
"""

HOME_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>home</title></head>
<body><h1>首页</h1><p id="whoami">logged-in</p></body></html>
"""


@dataclass
class SiteData:
    """What the fake platform will serve."""

    #: {keyword: [(item_id, title, author, likes)]}
    items_by_keyword: dict[str, list[tuple[str, str, str, int]]] = field(
        default_factory=lambda: {
            "咖啡": [
                ("note-1", "手冲咖啡入门", "咖啡爱好者", 128),
                ("note-2", "冷萃到底怎么做", "夏天不喝热的", 64),
            ],
            "手冲": [("note-3", "V60 手冲参数", "老王", 32)],
        }
    )
    #: {item_id: [(comment_id, author, content, likes)]}
    comments_by_item: dict[str, list[tuple[str, str, str, int]]] = field(
        default_factory=lambda: {
            "note-1": [
                ("c-1", "小明", "学到了！", 9),
                ("c-2", "小红", "水温多少？", 21),
            ],
            "note-2": [("c-3", "阿强", "夏天必备", 5)],
            "note-3": [],
        }
    )
    login_delay_ms: int = 800


class _Handler(BaseHTTPRequestHandler):
    site: SiteData  # injected per server instance

    # --- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):  # noqa: D102 - silence stderr spam
        pass

    def _send(self, body: str, content_type: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _html(self, body: str) -> None:
        self._send(body, "text/html; charset=utf-8")

    def _json(self, obj: dict) -> None:
        self._send(json.dumps(obj, ensure_ascii=False), "application/json")

    # --- routing ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        self._route()

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        self._route()

    def _route(self) -> None:
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path

        if path == "/":
            self._html(HOME_PAGE)
        elif path == "/login":
            self._html(LOGIN_PAGE.format(login_delay_ms=self.site.login_delay_ms))
        elif path == "/search":
            self._html(SEARCH_PAGE.format(
                keyword=query.get("keyword", ""), sort=query.get("sort", "general"),
            ))
        elif path.startswith("/explore/"):
            self._html(DETAIL_PAGE.format(item_id=path.rsplit("/", 1)[-1]))
        elif path == "/api/search":
            self._json(self._search_response(query.get("keyword", "")))
        elif path == "/api/feed":
            self._json(self._feed_response(query.get("id", "")))
        elif path == "/api/comments":
            self._json(self._comments_response(query.get("id", "")))
        else:
            self.send_error(404)

    # --- payloads ---------------------------------------------------------

    def _search_response(self, keyword: str) -> dict:
        rows = self.site.items_by_keyword.get(keyword, [])
        return {"data": {"items": [
            {
                "note_id": item_id,
                "display_title": title,
                "user": {"nickname": author},
                "interact_info": {"liked_count": likes},
            }
            for item_id, title, author, likes in rows
        ]}}

    def _feed_response(self, item_id: str) -> dict:
        title = next(
            (t for rows in self.site.items_by_keyword.values()
             for i, t, _a, _l in rows if i == item_id),
            f"未知笔记 {item_id}",
        )
        author = next(
            (a for rows in self.site.items_by_keyword.values()
             for i, _t, a, _l in rows if i == item_id),
            "unknown",
        )
        return {"note": {
            "note_id": item_id,
            "title": title,
            "desc": f"{title} 的正文内容，足够长以便断言。",
            "type": "normal",
            "time": 1690000000000,
            "user": {"nickname": author},
            "image_list": [f"http://127.0.0.1/{item_id}-1.jpg"],
            "tag_list": ["咖啡", "教程"],
            "interact_info": {
                "liked_count": 128, "collected_count": 12,
                "comment_count": len(self.site.comments_by_item.get(item_id, [])),
                "share_count": 3,
            },
        }}

    def _comments_response(self, item_id: str) -> dict:
        rows = self.site.comments_by_item.get(item_id, [])
        return {"data": {"comments": [
            {
                "id": cid,
                "user": {"nickname": author},
                "content": content,
                "like_count": likes,
            }
            for cid, author, content, likes in rows
        ]}}


class LocalSite:
    """Runs :class:`SiteData` on a real localhost HTTP server."""

    def __init__(self, data: SiteData | None = None):
        self.data = data or SiteData()
        handler = type("BoundHandler", (_Handler,), {"site": self.data})
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> "LocalSite":
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

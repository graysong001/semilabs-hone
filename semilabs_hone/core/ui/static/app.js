/* semilabs-hone — WebSocket client + 全局交互脚本 (ui_design_spec_v2 §11).
 *
 * 保留: WS 连接/重连退避 + 消息分发; CSV 导出; HTMX 错误 Toast。
 * showToast 已统一为规范 §11 的 (msg, type) 内联样式签名, 同时兼容旧的 {severity,message,duration} 对象调用。
 * 表单交互 JS (radio-card / count-btn / submitWithEstimate 等) 见后续块按需追加。
 */
(function () {
  "use strict";

  /* ========== Toast (规范 §11, 内联样式, 暗色科技风) ========== */
  var TOAST_BG = { warning: "#FEB019", warn: "#FEB019", error: "#FF4560", success: "#00E396", info: "#2A303D" };

  function showToast(msg, type) {
    var message, severity, duration;
    if (msg && typeof msg === "object") {
      severity = msg.severity || "info";
      message = msg.message || JSON.stringify(msg);
      duration = msg.duration || 3000;
    } else {
      message = msg;
      severity = type || "info";
      duration = 3000;
    }
    var container = document.getElementById("toast-container");
    if (!container) {
      container = document.createElement("div");
      container.id = "toast-container";
      document.body.appendChild(container);
    }
    var el = document.createElement("div");
    el.style.cssText =
      "background:" + (TOAST_BG[severity] || TOAST_BG.info) + ";color:#fff;padding:10px 20px;border-radius:8px;" +
      "font-size:13px;box-shadow:0 4px 12px rgba(0,0,0,0.3);animation:slideIn 0.3s;" +
      "border:1px solid rgba(255,255,255,0.1);max-width:320px;";
    el.textContent = message;
    container.appendChild(el);
    setTimeout(function () {
      el.style.transition = "opacity 0.3s";
      el.style.opacity = "0";
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 300);
    }, duration);
  }
  // 兼容两种调用: showToast("文本","error") 与 showToast({severity,message,duration})
  window.showToast = showToast;

  /* ========== CSV 导出 (PRD §4.6) ========== */
  window.exportCsv = function (taskId, btn) {
    var url = "/api/export" + (taskId ? "?task_id=" + encodeURIComponent(taskId) : "");
    if (btn) { btn.setAttribute("aria-busy", "true"); btn.disabled = true; }
    fetch(url)
      .then(function (resp) {
        if (!resp.ok) {
          return resp.json().catch(function () { return { error: "导出失败" }; })
            .then(function (body) {
              showToast({ severity: "warn", message: body.error || "暂无可导出的采集数据", duration: 3000 });
              throw new Error("export-empty");
            });
        }
        var disp = resp.headers.get("content-disposition") || "";
        var m = disp.match(/filename="?([^"]+)"?/i);
        var filename = m ? m[1] : "export.csv";
        return resp.blob().then(function (blob) {
          var a = document.createElement("a");
          var objUrl = URL.createObjectURL(blob);
          a.href = objUrl;
          a.download = filename;
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(objUrl);
        });
      })
      .catch(function () { /* empty/error 已 toast */ })
      .finally(function () {
        if (btn) { btn.removeAttribute("aria-busy"); btn.disabled = false; }
      });
  };

  /* ========== 全局 HTMX 错误 Toast ========== */
  document.addEventListener("htmx:responseError", function () {
    showToast({ severity: "error", message: "系统异常，操作失败，请检查后台日志", duration: 3000 });
  });
  document.addEventListener("htmx:sendError", function () {
    showToast({ severity: "error", message: "系统异常，操作失败，请检查后台日志", duration: 3000 });
  });

  /* ========== WebSocket 客户端 (连接/重连退避/消息分发) ========== */
  var RECONNECT_DELAY = 2000;
  var MAX_RECONNECT_DELAY = 30000;
  var ws = null;
  var currentDelay = RECONNECT_DELAY;
  var wsStatus = document.getElementById("ws-status");
  var wsDot = wsStatus ? wsStatus.querySelector(".ws-dot") : null;

  function setWsState(connected) {
    if (!wsDot) return;
    if (connected) { wsDot.classList.add("connected"); currentDelay = RECONNECT_DELAY; }
    else { wsDot.classList.remove("connected"); }
  }

  function dispatch(msg) {
    var type = msg.type;
    switch (type) {
      case "progress": updateProgress(msg); break;
      case "warn":
      case "disk_warn": showToast({ severity: "warn", message: msg.message }); break;
      case "error": showToast({ severity: "error", message: msg.message }); break;
      case "qr_ready": showToast({ severity: "info", message: "扫码已就绪，请在 Chrome 中完成登录" }); break;
      case "captcha_required": showToast({ severity: "warn", message: "需要验证码，请在 Chrome 中完成验证" }); break;
      case "task_completed": showToast({ severity: "info", message: "任务完成: " + (msg.task_id || "") }); break;
      case "login_success": showToast({ severity: "info", message: "登录成功" }); break;
      default: break;
    }
    // 供页面级监听 (如 task_detail) 的自定义事件
    document.dispatchEvent(new CustomEvent("ws:message", { detail: msg }));
  }

  function updateProgress(msg) {
    var data = msg.data || {};
    var barId = "progress-" + (msg.task_id || msg.request_id || "");
    var bar = document.getElementById(barId);
    if (bar) { var pct = data.percent || 0; bar.style.width = pct + "%"; }
    var log = document.getElementById("task-log");
    if (log && msg.message) { log.textContent += msg.message + "\n"; }
  }

  function connect() {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var url = proto + "//" + location.host + "/ws";
    ws = new WebSocket(url);
    ws.onopen = function () { setWsState(true); currentDelay = RECONNECT_DELAY; };
    ws.onclose = function () {
      setWsState(false);
      setTimeout(connect, currentDelay);
      currentDelay = Math.min(currentDelay * 1.5, MAX_RECONNECT_DELAY);
    };
    ws.onerror = function () { setWsState(false); };
    ws.onmessage = function (event) {
      try { dispatch(JSON.parse(event.data)); } catch (e) { /* ignore non-JSON */ }
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", connect);
  } else {
    connect();
  }
})();

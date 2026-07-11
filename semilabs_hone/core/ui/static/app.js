/* semilabs-hone — WebSocket client + message dispatch.
 *
 * Manages a single WS connection to /ws with auto-reconnect.
 * Dispatches messages by msg.type: progress / warn / qr_ready /
 * captcha_required / error / disk_warn / task_completed / login_success.
 */
(function () {
  "use strict";

  var RECONNECT_DELAY = 2000;
  var MAX_RECONNECT_DELAY = 30000;
  var ws = null;
  var currentDelay = RECONNECT_DELAY;

  var wsStatus = document.getElementById("ws-status");
  var wsDot = wsStatus ? wsStatus.querySelector(".ws-dot") : null;

  function setWsState(connected) {
    if (!wsDot) return;
    if (connected) {
      wsDot.classList.add("connected");
      currentDelay = RECONNECT_DELAY;
    } else {
      wsDot.classList.remove("connected");
    }
  }

  function showToast(msg) {
    var container = document.getElementById("toast-container");
    if (!container) {
      container = document.createElement("div");
      container.id = "toast-container";
      container.className = "toast-container";
      document.body.appendChild(container);
    }
    var el = document.createElement("div");
    el.className = "toast " + (msg.severity || "info");
    el.textContent = msg.message || JSON.stringify(msg);
    container.appendChild(el);
    setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, msg.duration || 5000);
  }

  // Expose for inline scripts (e.g. task_new.html success toast).
  window.showToast = showToast;

  // Global HTMX error Toast (PRD §5.1.2): responseError/sendError → 右上红 Toast 3s.
  document.addEventListener("htmx:responseError", function () {
    showToast({ severity: "error", message: "系统异常，操作失败，请检查后台日志", duration: 3000 });
  });
  document.addEventListener("htmx:sendError", function () {
    showToast({ severity: "error", message: "系统异常，操作失败，请检查后台日志", duration: 3000 });
  });

  function dispatch(msg) {
    var type = msg.type;
    switch (type) {
      case "progress":
        updateProgress(msg);
        break;
      case "warn":
      case "disk_warn":
        showToast({ severity: "warn", message: msg.message });
        break;
      case "error":
        showToast({ severity: "error", message: msg.message });
        break;
      case "qr_ready":
        showToast({ severity: "info", message: "扫码已就绪，请在 Chrome 中完成登录" });
        break;
      case "captcha_required":
        showToast({ severity: "warn", message: "需要验证码，请在 Chrome 中完成验证" });
        break;
      case "task_completed":
        showToast({ severity: "info", message: "任务完成: " + (msg.task_id || "") });
        break;
      case "login_success":
        showToast({ severity: "info", message: "登录成功" });
        break;
      default:
        break;
    }
  }

  function updateProgress(msg) {
    var data = msg.data || {};
    var barId = "progress-" + (msg.task_id || msg.request_id || "");
    var bar = document.getElementById(barId);
    if (bar) {
      var pct = data.percent || 0;
      bar.style.width = pct + "%";
    }
    var log = document.getElementById("task-log");
    if (log && msg.message) {
      log.textContent += msg.message + "\n";
    }
  }

  function connect() {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var url = proto + "//" + location.host + "/ws";
    ws = new WebSocket(url);

    ws.onopen = function () {
      setWsState(true);
      currentDelay = RECONNECT_DELAY;
    };

    ws.onclose = function () {
      setWsState(false);
      setTimeout(connect, currentDelay);
      currentDelay = Math.min(currentDelay * 1.5, MAX_RECONNECT_DELAY);
    };

    ws.onerror = function () {
      setWsState(false);
    };

    ws.onmessage = function (event) {
      try {
        var msg = JSON.parse(event.data);
        dispatch(msg);
      } catch (e) {
        // ignore non-JSON messages
      }
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", connect);
  } else {
    connect();
  }
})();

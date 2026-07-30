/* semilabs-hone — WebSocket client + 全局交互脚本 (ui_design_spec_v2 §11).
 *
 * 保留: WS 连接/重连退避 + 消息分发; CSV 导出; HTMX 错误 Toast。
 * showToast 已统一为规范 §11 的 (msg, type) 内联样式签名, 同时兼容旧的 {severity,message,duration} 对象调用。
 * 表单交互 JS (radio-card / count-btn / submitWithEstimate 等) 见后续块按需追加。
 *
 * [main 调和] updateProgress 用 data-progress-for 选择器契约（进度条元素标记
 * data-progress-for="<task_id|request_id>"，页面级富渲染走 ws:message）；
 * dispatch 补 session_status case（账号页会话验证反馈）。
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

  /* ========== 全局 HTMX 错误 Toast ==========
   * 后端 4xx/409 的 JSON 契约是 {error, fix_hint?}（G8 pre-flight / 删除守卫 / cookie 校验），
   * 必须透出具体原因——“系统异常”对可自愈的用户错误是误导。按钮级不再重复 toast。 */
  function toastResponseError(xhr) {
    var msg = "系统异常，操作失败，请检查后台日志";
    try {
      var body = JSON.parse(xhr.responseText);
      if (body && body.error) {
        msg = body.error + (body.fix_hint ? "：" + body.fix_hint : "");
      }
    } catch (e) { /* 非 JSON 响应用兜底文案 */ }
    showToast({ severity: "error", message: msg, duration: 4000 });
  }
  document.addEventListener("htmx:responseError", function (evt) {
    if (evt.detail && evt.detail.xhr) toastResponseError(evt.detail.xhr);
  });
  document.addEventListener("htmx:sendError", function () {
    showToast({ severity: "error", message: "系统异常，操作失败，请检查后台日志", duration: 3000 });
  });

  /* ========== 块2: 新建任务弹窗交互 (规范 §11) ========== */
  // radio-card 点击
  document.querySelectorAll('.radio-card').forEach(function(card) {
    card.addEventListener('click', function() {
      card.parentElement.querySelectorAll('.radio-card').forEach(function(c) { c.classList.remove('selected'); });
      card.classList.add('selected');
      card.querySelector('input[type="radio"]').checked = true;
    });
  });

  // 任务类型切换 (keyword_search / author_homepage)
  // 两个字段同名 target_value：隐藏的那个必须 disabled，否则表单会带上两个
  // 同名字段，starlette 取最后一个值导致选中的内容被空值覆盖。
  window.toggleTaskTypeFields = function() {
    var type = document.getElementById('task-type-select').value;
    var keywordField = document.getElementById('keyword-field');
    var urlField = document.getElementById('url-field');
    var keywordInput = keywordField.querySelector('[name="target_value"]');
    var urlInput = urlField.querySelector('[name="target_value"]');
    if (type === 'keyword_search') {
      keywordField.classList.remove('hidden');
      urlField.classList.add('hidden');
      keywordInput.disabled = false;
      urlInput.disabled = true;
    } else {
      keywordField.classList.add('hidden');
      urlField.classList.remove('hidden');
      keywordInput.disabled = true;
      urlInput.disabled = false;
    }
  };

  // count-btn 点击
  document.querySelectorAll('.count-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      document.querySelectorAll('.count-btn').forEach(function(b) { b.classList.remove('active'); });
      btn.classList.add('active');
      document.querySelector('[name="expected_count"]').value = btn.dataset.count;
    });
  });

  // 开始采集 → 显示耗时预估弹窗
  window.submitWithEstimate = function() {
    var form = document.getElementById('create-task-form');
    var count = parseInt(form.querySelector('[name="expected_count"]').value);
    if (count > 200) {
      form.querySelector('[name="expected_count"]').value = 200;
      showToast({ severity: 'warn', message: '单任务上限 200 条，已自动调整', duration: 3000 });
      return;
    }
    if (count < 1) {
      showToast({ severity: 'error', message: '数量不能小于 1', duration: 3000 });
      return;
    }
    // 预估耗时: 每条 1-1.5 分钟, 每 5 条加 1 分钟休息
    var groups = Math.floor(count / 5);
    var minMin = Math.round(count * 1.0 + groups * 1.0);
    var maxMin = Math.round(count * 1.5 + groups * 1.5);
    document.getElementById('estimate-minutes').textContent = minMin + '-' + maxMin;
    document.getElementById('create-modal').close();
    document.getElementById('estimate-modal').showModal();
  };

  // 确认挂机 → 提交表单
  window.confirmCreate = function() {
    document.getElementById('estimate-modal').close();
    var form = document.getElementById('create-task-form');
    htmx.trigger(form, 'submit');
  };

  // HTMX 提交后处理 (解析 JSON, 插入新行到 tbody)
  window.handleTaskCreateResponse = function(event) {
    if (event.detail.successful) {
      var xhr = event.detail.xhr;
      try {
        var data = JSON.parse(xhr.responseText);
        if (data.status === 'ok' || data.task_id) {
          showToast({ severity: 'success', message: '任务已创建', duration: 2000 });
          // 如果有 task_id, 用 HTMX 拉新行 HTML 插入到 tbody 顶部
          if (data.task_id) {
            var tbody = document.getElementById('task-table-body');
            if (tbody) {
              htmx.ajax('GET', '/api/tasks/' + data.task_id + '/row', { target: tbody, swap: 'afterbegin' });
            }
          }
        } else {
          showToast({ severity: 'error', message: data.error || '创建失败', duration: 3000 });
        }
      } catch (e) {
        showToast({ severity: 'error', message: '响应解析失败', duration: 3000 });
      }
    } else {
      showToast({ severity: 'error', message: '创建失败', duration: 3000 });
    }
  };

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
      case "session_status":
        showToast({
          severity: msg.valid ? "info" : "warn",
          message: msg.message || (msg.valid ? "会话有效" : "会话已失效"),
        });
        break;
      case "cookie_import_conflict":
        showToast({
          severity: "error",
          message: msg.message || "该平台身份已绑定其他账号，Cookie 未生效",
          duration: 6000,
        });
        break;
      default: break;
    }
    // 供页面级监听 (如 task_detail) 的自定义事件
    document.dispatchEvent(new CustomEvent("ws:message", { detail: msg }));
  }

  function updateProgress(msg) {
    var data = msg.data || {};
    // Progress bars are marked with data-progress-for="<task_id|request_id>";
    // page-level rich rendering (log lines, counters) happens via ws:message.
    var key = msg.task_id || msg.request_id || "";
    var bar = document.querySelector('[data-progress-for="' + key + '"]');
    if (bar && typeof data.percent === "number") {
      bar.style.width = data.percent + "%";
      bar.setAttribute("aria-valuenow", data.percent);
    }
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

  /* ========== Block 5: Risk Control Modal ========== */
  window.openRiskModal = function(taskId, taskTarget) {
    var modal = document.getElementById('risk-control-modal');
    if (!modal) return;
    document.getElementById('risk-task-id').textContent = taskId.substring(0, 8);
    document.getElementById('risk-task-target').textContent = taskTarget || '—';

    // Set up button handlers
    document.getElementById('risk-activate-btn').onclick = function() {
      fetch('/api/tasks/' + taskId + '/activate-browser', { method: 'POST' })
        .then(function(resp) { return resp.json(); })
        .then(function(data) {
          if (data.ok) {
            showToast({ severity: 'success', message: 'Chrome 已唤起', duration: 2000 });
          } else {
            showToast({ severity: 'error', message: data.error || '唤起失败', duration: 3000 });
          }
        })
        .catch(function() {
          showToast({ severity: 'error', message: '网络错误', duration: 3000 });
        });
    };

    document.getElementById('risk-resume-btn').onclick = function() {
      fetch('/api/tasks/' + taskId + '/resume', { method: 'POST' })
        .then(function(resp) { return resp.json(); })
        .then(function(data) {
          if (data.ok) {
            showToast({ severity: 'success', message: '任务已恢复', duration: 2000 });
            modal.close();
            // Refresh action buttons (status changed need_human → running)
            htmx.ajax('GET', '/api/tasks/' + taskId + '/actions', '#actions-' + taskId);
          } else {
            showToast({ severity: 'error', message: data.error || '恢复失败', duration: 3000 });
          }
        })
        .catch(function() {
          showToast({ severity: 'error', message: '网络错误', duration: 3000 });
        });
    };

    modal.showModal();
  };
})();

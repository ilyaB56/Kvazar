// Экран настройки адреса (§5.1). IPC: команды крейта оболочки.
const invoke = window.__TAURI__.core.invoke;
const $ = (id) => document.getElementById(id);

let pendingAddress = null;

async function init() {
  // подставить сохранённый адрес и настройки, если они есть (смена
  // адреса §5.3; настройки — этап B)
  try {
    const st = await invoke("shell_state");
    if (st.server_url) $("address").value = st.server_url;
  } catch (e) { /* первый запуск — дефолт в разметке */ }
  try {
    const s = await invoke("settings_state");
    if (s.server_url) $("address").value = s.server_url;
    const recent = $("recent");
    (s.recent_addresses || []).forEach((a) => {
      const opt = document.createElement("option");
      opt.value = a;
      recent.appendChild(opt);
    });
    $("opt-close-tray").checked = !!s.close_to_tray;
    $("opt-autostart").checked = !!s.autostart;
    $("opt-notifications").checked = !!s.notifications_enabled;
    $("version").textContent = "Оболочка Квазар, версия " + (s.version || "");
  } catch (e) { /* настройки недоступны — экран работает как первый запуск */ }
  $("address").focus();
}

function bindOption(id, key) {
  $(id).addEventListener("change", async (ev) => {
    try {
      await invoke("set_setting", { key, value: ev.target.checked });
    } catch (e) {
      ev.target.checked = !ev.target.checked;
    }
  });
}
bindOption("opt-close-tray", "close_to_tray");
bindOption("opt-autostart", "autostart");
bindOption("opt-notifications", "notifications_enabled");

$("test-note").addEventListener("click", () => {
  invoke("test_notification").catch(() => {});
});

$("check").addEventListener("click", async () => {
  hideWarn();
  $("error").textContent = "";
  try {
    const out = await invoke("connect", { address: $("address").value });
    if (out === "not_kvazar") {
      pendingAddress = $("address").value;
      showWarn();
    }
    // "connected" — переход делает оболочка (navigate на server_url)
  } catch (e) {
    $("error").textContent = String(e);
  }
});

$("save").addEventListener("click", async () => {
  hideWarn();
  $("error").textContent = "";
  try {
    await invoke("connect_without_check", { address: $("address").value });
  } catch (e) {
    $("error").textContent = String(e);
  }
});

$("force").addEventListener("click", async () => {
  $("error").textContent = "";
  try {
    await invoke("connect_without_check", { address: pendingAddress });
  } catch (e) {
    $("error").textContent = String(e);
  }
});

$("edit").addEventListener("click", () => {
  hideWarn();
  $("address").focus();
});

function showWarn() {
  $("warn").classList.add("visible");
  $("warn-actions").style.display = "flex";
}
function hideWarn() {
  $("warn").classList.remove("visible");
  $("warn-actions").style.display = "none";
}

init();

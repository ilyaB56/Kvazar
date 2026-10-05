// Экран настройки адреса (§5.1). IPC: команды крейта оболочки.
const invoke = window.__TAURI__.core.invoke;
const $ = (id) => document.getElementById(id);

let pendingAddress = null;

async function init() {
  // подставить сохранённый адрес, если он есть (смена адреса, §5.3)
  try {
    const st = await invoke("shell_state");
    if (st.server_url) $("address").value = st.server_url;
  } catch (e) { /* первый запуск — дефолт в разметке */ }
  $("address").focus();
}

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

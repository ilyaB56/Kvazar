// Экран-заглушка «Квазар не запущен» (§6): адрес + ручной повтор /
// смена адреса; автоповтор зонда с бэкоффом — в оболочке (Rust).
const invoke = window.__TAURI__.core.invoke;

invoke("shell_state").then((st) => {
  if (st.server_url) document.getElementById("addr").textContent = st.server_url;
});

document.getElementById("retry").addEventListener("click", () => {
  invoke("retry_now");
});
document.getElementById("settings").addEventListener("click", () => {
  invoke("open_settings");
});

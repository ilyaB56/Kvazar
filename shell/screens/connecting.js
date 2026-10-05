// Экран «Подключаемся» (§6): только отображение адреса; переходы
// (успех → интерфейс, 10 с безуспешности → «не запущен») делает оболочка.
window.__TAURI__.core.invoke("shell_state").then((st) => {
  if (st.server_url) document.getElementById("addr").textContent = st.server_url;
});

// Первый диалог закрытия окна (§7.3, этап B): выбор «трей/выход» с
// чекбоксом «Запомнить выбор» (паттерн Telegram).
const invoke = window.__TAURI__.core.invoke;
const remember = () => document.getElementById("remember").checked;

document.getElementById("tray").addEventListener("click", () => {
  invoke("close_dialog_answer", { choice: "tray", remember: remember() });
});
document.getElementById("exit").addEventListener("click", () => {
  invoke("close_dialog_answer", { choice: "exit", remember: remember() });
});

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import '@fontsource/geist-sans/400.css'
import '@fontsource/geist-sans/500.css'
import '@fontsource/geist-sans/700.css'
import '@fontsource/geist-mono/400.css'
import './styles/tokens.css'
import App from './App.vue'
import router from './router'
import { i18n } from './i18n'
import './styles.css'

// tauri-shell-spec §13.1: фиксация признака оболочки (?shell=tauri) —
// query сбрасывается первой же внутренней навигацией, поэтому запоминаем
// в sessionStorage (переживает перезагрузки страницы в окне оболочки)
if (new URLSearchParams(window.location.search).get('shell') === 'tauri') {
  sessionStorage.setItem('erp-shell', 'tauri')
}

// тема применяется до монтирования — без вспышки светлого при dark
const storedTheme = localStorage.getItem('erp-theme')
if (storedTheme === 'dark') {
  document.documentElement.classList.add('dark')
}

createApp(App).use(createPinia()).use(router).use(i18n).mount('#app')

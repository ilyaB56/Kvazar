import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
// переходный период (этапы A–G): старые экраны на Element Plus должны иметь
// тёмную пару — официальные css-перементы EP реагируют на наш .dark на <html>
import 'element-plus/theme-chalk/dark/css-vars.css'
import '@fontsource/geist-sans/400.css'
import '@fontsource/geist-sans/500.css'
import '@fontsource/geist-sans/700.css'
import '@fontsource/geist-mono/400.css'
import './styles/tokens.css'
import App from './App.vue'
import router from './router'
import { i18n } from './i18n'
import './styles.css'

// тема применяется до монтирования — без вспышки светлого при dark
const storedTheme = localStorage.getItem('erp-theme')
if (storedTheme === 'dark') {
  document.documentElement.classList.add('dark')
}

createApp(App).use(createPinia()).use(router).use(i18n).use(ElementPlus).mount('#app')

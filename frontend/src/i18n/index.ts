import { createI18n } from 'vue-i18n'
import ru from './ru'

// Все строки UI — ключи; v1 только словарь ru (английский/китайский позже).
export const i18n = createI18n({
  legacy: false,
  locale: 'ru',
  fallbackLocale: 'ru',
  messages: { ru },
})

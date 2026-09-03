import { createI18n } from 'vue-i18n'
import ru from './ru'
import en from './en'

// Все строки UI — ключи; активная локаль ru, en — с первого дня экранов
// редизайна (спека §9); старые ключи пока ru-only, en фолбэчится на ru.
export const i18n = createI18n({
  legacy: false,
  locale: 'ru',
  fallbackLocale: 'ru',
  messages: { ru, en },
})

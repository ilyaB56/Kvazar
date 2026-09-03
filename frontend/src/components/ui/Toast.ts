// Toast: composable useToast по образцу макета (§4). Хост монтируется в ErpShell.
import { reactive } from 'vue'
import { useI18n } from 'vue-i18n'

export interface ToastItem {
  id: number
  title: string
  description?: string
  tone: 'default' | 'success' | 'error'
}

const state = reactive<{ items: ToastItem[] }>({ items: [] })
let counter = 0

export function useToast() {
  const { t } = useI18n()

  function push(item: Omit<ToastItem, 'id' | 'tone'> & { tone?: ToastItem['tone'] }, timeoutMs = 4500) {
    const id = ++counter
    state.items.push({ id, tone: item.tone ?? 'default', title: item.title, description: item.description })
    window.setTimeout(() => dismiss(id), timeoutMs)
    return id
  }

  function dismiss(id: number) {
    const index = state.items.findIndex((toast) => toast.id === id)
    if (index !== -1) state.items.splice(index, 1)
  }

  return {
    state,
    dismiss,
    success: (title: string, description?: string) => push({ title, description, tone: 'success' }),
    error: (title: string, description?: string) => push({ title, description, tone: 'error' }),
    /** Ошибки запроса: подставляет человекочитаемый тост с текстом exception */
    apiError: (error: unknown) =>
      push({
        title: t('ui.errorTitle'),
        description: error instanceof Error ? error.message : t('ui.errorUnknown'),
        tone: 'error',
      }),
  }
}

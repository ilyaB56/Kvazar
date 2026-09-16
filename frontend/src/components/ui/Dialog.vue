<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const props = withDefaults(defineProps<{ open: boolean; title?: string; width?: string }>(), {
  title: '',
  width: '32rem',
})
const emit = defineEmits<{ 'update:open': [value: boolean] }>()
const { t } = useI18n()

const root = ref<HTMLElement | null>(null)
let previouslyFocused: HTMLElement | null = null

function close() {
  emit('update:open', false)
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') close()
  if (event.key === 'Tab' && root.value) {
    // фокус-ловушка: Tab кружит внутри диалога
    const focusable = root.value.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    )
    if (!focusable.length) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      previouslyFocused = document.activeElement as HTMLElement | null
      document.addEventListener('keydown', onKeydown)
      await nextTick()
      root.value?.querySelector<HTMLElement>('input, textarea, button')?.focus()
    } else {
      document.removeEventListener('keydown', onKeydown)
      previouslyFocused?.focus()
    }
  },
)
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div class="absolute inset-0 bg-black/50" aria-hidden="true" @click="close" />
      <div
        ref="root"
        role="dialog"
        aria-modal="true"
        :aria-label="title || undefined"
        class="relative z-10 w-full rounded-xl border border-border bg-card p-6 shadow-lg"
        :style="{ maxWidth: width }"
      >
        <div class="mb-4 flex items-center justify-between gap-4">
          <h2 v-if="title" class="min-w-0 truncate text-lg font-semibold leading-snug tracking-tight">{{ title }}</h2>
          <button
            type="button"
            class="shrink-0 rounded-md text-muted-foreground transition-colors hover:text-foreground"
            :aria-label="t('ui.close')"
            @click="close"
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </div>
        <slot />
      </div>
    </div>
  </Teleport>
</template>

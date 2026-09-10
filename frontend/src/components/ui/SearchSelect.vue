<script setup lang="ts">
// SearchSelect (гейт 1.1a): выбор объекта из больших справочников (1707
// товаров, 849 контрагентов) — поле фильтрует по подстроке (value+label),
// клавиатура ↑/↓/Enter/Esc, клик вне закрывает. Дропдаун срежет до 50
// совпадений — проматывать руками больше не нужно.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Search } from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    modelValue?: string
    options: Array<{ value: string; label: string }>
    placeholder?: string
    searchPlaceholder?: string
    disabled?: boolean
  }>(),
  { modelValue: '', placeholder: '', searchPlaceholder: '', disabled: false },
)
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const open = ref(false)
const query = ref('')
const highlight = ref(0)
const root = ref<HTMLElement | null>(null)
const input = ref<HTMLInputElement | null>(null)

const selected = computed(() =>
  props.options.find((option) => option.value === props.modelValue))

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  const src = q
    ? props.options.filter((option) => option.label.toLowerCase().includes(q))
    : props.options
  return src.slice(0, 50)
})

function toggle() {
  if (props.disabled) return
  open.value = !open.value
  if (open.value) {
    query.value = ''
    highlight.value = 0
  }
}

function choose(value: string) {
  emit('update:modelValue', value)
  open.value = false
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    highlight.value = Math.min(highlight.value + 1, filtered.value.length - 1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    highlight.value = Math.max(highlight.value - 1, 0)
  } else if (event.key === 'Enter') {
    event.preventDefault()
    const option = filtered.value[highlight.value]
    if (option) choose(option.value)
  } else if (event.key === 'Escape') {
    open.value = false
  }
}

function onDocClick(event: MouseEvent) {
  if (open.value && root.value && !root.value.contains(event.target as Node)) {
    open.value = false
  }
}

onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))
watch(open, async (value) => {
  if (value) {
    highlight.value = Math.max(filtered.value.findIndex((o) => o.value === props.modelValue), 0)
    await new Promise((resolve) => setTimeout(resolve, 0))
    input.value?.focus()
  }
})
</script>

<template>
  <div ref="root" class="relative">
    <button
      type="button" :disabled="disabled"
      class="flex h-9 w-full items-center justify-between gap-2 rounded-lg border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"
      @click="toggle"
    >
      <span class="min-w-0 flex-1 truncate text-left" :class="!selected && 'text-muted-foreground'">
        {{ selected?.label ?? (placeholder || '—') }}
      </span>
      <svg class="h-4 w-4 shrink-0 opacity-50" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="m6 9 6 6 6-6" />
      </svg>
    </button>

    <div
      v-if="open"
      class="absolute z-50 mt-1 w-full min-w-[240px] rounded-lg border border-border bg-popover p-1.5 text-popover-foreground shadow-lg"
    >
      <div class="relative mb-1">
        <Search class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <input
          ref="input" v-model="query" type="text"
          :placeholder="searchPlaceholder"
          class="h-8 w-full rounded-md border border-input bg-background pl-8 pr-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          @keydown="onKeydown"
        >
      </div>
      <div class="max-h-64 overflow-y-auto erp-scroll" role="listbox">
        <button
          v-for="(option, index) in filtered" :key="option.value"
          type="button" role="option" :aria-selected="option.value === modelValue"
          :class="[
            'flex w-full items-center rounded-md px-2 py-1.5 text-left text-sm transition-colors',
            index === highlight ? 'bg-accent text-accent-foreground' : 'hover:bg-accent',
            option.value === modelValue && 'font-semibold text-emerald-700 dark:text-emerald-400',
          ]"
          @mouseenter="highlight = index"
          @click="choose(option.value)"
        >
          <span class="min-w-0 flex-1 truncate">{{ option.label }}</span>
        </button>
        <p v-if="filtered.length === 0" class="px-2 py-3 text-center text-xs text-muted-foreground">
          {{ query ? '—' : '…' }}
        </p>
      </div>
    </div>
  </div>
</template>

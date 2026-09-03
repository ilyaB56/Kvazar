<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = withDefaults(defineProps<{ align?: 'start' | 'end' }>(), { align: 'end' })
const open = ref(false)
const root = ref<HTMLElement | null>(null)

function toggle() {
  open.value = !open.value
}
// клик-вне и Esc — на document: панель меню лежит внутри sticky-шапки
// (собственный stacking context), полноэкранный backdrop её бы перекрыл
function onDocClick(event: MouseEvent) {
  if (open.value && root.value && !root.value.contains(event.target as Node)) open.value = false
}
function onDocKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') open.value = false
}
onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onDocKeydown)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onDocKeydown)
})
watch(open, async (value) => {
  if (value) {
    await nextTick()
    root.value?.querySelector<HTMLElement>('button:not([data-trigger]), [role="menuitem"]')?.focus()
  }
})
</script>

<template>
  <div ref="root" class="relative inline-block">
    <button type="button" data-trigger :aria-expanded="open" aria-haspopup="menu" @click="toggle">
      <slot name="trigger" />
    </button>
    <div
      v-if="open"
      role="menu"
      class="absolute z-50 mt-1 min-w-44 overflow-hidden rounded-xl border border-border bg-popover p-1 text-popover-foreground shadow-lg"
      :class="align === 'end' ? 'right-0' : 'left-0'"
      @click="open = false"
      @keydown.escape="open = false"
    >
      <div v-if="$slots.label" class="px-2 py-1.5 text-xs font-medium text-muted-foreground">
        <slot name="label" />
      </div>
      <slot />
    </div>
  </div>
</template>

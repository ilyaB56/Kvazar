<script setup lang="ts">
// Единый паттерн заголовка экрана/секции (дизайн-аудит 2026-09-15):
// строка «заголовок + поиск + кнопка» — flex-wrap gap-3, заголовок
// min-w-0 truncate, действия shrink-0. Больше не копируется вручную.
defineProps<{ title?: string; subtitle?: string }>()
</script>

<template>
  <div class="flex flex-wrap items-center justify-between gap-3">
    <div class="min-w-0">
      <h1 class="truncate text-xl font-semibold leading-snug tracking-tight text-foreground sm:text-2xl">
        <slot name="title" />{{ title }}
      </h1>
      <p v-if="subtitle || $slots.subtitle" class="mt-0.5 truncate text-sm text-muted-foreground">
        <slot name="subtitle" />{{ subtitle }}
      </p>
    </div>
    <div v-if="$slots.actions" class="flex shrink-0 flex-wrap items-center gap-2">
      <slot name="actions" />
    </div>
  </div>
</template>

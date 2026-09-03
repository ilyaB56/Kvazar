<script setup lang="ts">
defineProps<{ title?: string; subtitle?: string }>()
</script>

<template>
  <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
    <div>
      <h1 class="text-xl font-semibold tracking-tight text-foreground sm:text-2xl">
        <slot name="title" />{{ title }}
      </h1>
      <p v-if="subtitle || $slots.subtitle" class="mt-0.5 text-sm text-muted-foreground">
        <slot name="subtitle" />{{ subtitle }}
      </p>
    </div>
    <div v-if="$slots.actions" class="flex flex-wrap items-center gap-2">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ initials?: string; size?: 'md' | 'lg' }>(), { size: 'md' })
</script>
<template>
  <span :class="['relative flex shrink-0 overflow-hidden rounded-full', size === 'lg' ? 'h-11 w-11' : 'h-8 w-8']">
    <span class="flex h-full w-full items-center justify-center rounded-full bg-emerald-100 text-xs font-bold text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
      :class="size === 'lg' && 'text-sm'">
      {{ initials }}
    </span>
  </span>
</template>

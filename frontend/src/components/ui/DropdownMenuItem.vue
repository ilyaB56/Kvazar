<script setup lang="ts">
import { cn } from '../../lib/utils'
withDefaults(defineProps<{ destructive?: boolean; disabled?: boolean }>(), {
  destructive: false,
  disabled: false,
})
</script>

<template>
  <button
    type="button"
    role="menuitem"
    :disabled="disabled"
    :class="cn(
      'flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-sm transition-colors text-left',
      disabled && 'cursor-default text-muted-foreground',
      destructive
        ? 'text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40'
        : !disabled && 'hover:bg-accent hover:text-accent-foreground',
    )"
  >
    <slot />
  </button>
</template>

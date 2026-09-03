<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '../../lib/utils'
const props = withDefaults(defineProps<{ variant?: 'default' | 'outline' | 'secondary' }>(), { variant: 'default' })
const klass = computed(() => ({
  default: 'border-transparent bg-primary text-primary-foreground',
  outline: 'text-foreground',
  secondary: 'border-transparent bg-secondary text-secondary-foreground',
}[props.variant]))
</script>
<template>
  <span :class="cn('inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors', klass)">
    <slot />
  </span>
</template>

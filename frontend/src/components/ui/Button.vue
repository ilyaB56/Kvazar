<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '../../lib/utils'

const props = withDefaults(defineProps<{
  variant?: 'default' | 'emerald' | 'outline' | 'ghost' | 'destructive'
  size?: 'sm' | 'md' | 'icon'
  type?: 'button' | 'submit'
  disabled?: boolean
}>(), { variant: 'default', size: 'md', type: 'button', disabled: false })

const variantClass = computed(() => ({
  default: 'bg-primary text-primary-foreground hover:bg-primary/90',
  emerald: 'bg-emerald-500 text-white hover:bg-emerald-600 shadow-sm',
  outline: 'border border-border bg-background hover:bg-accent hover:text-accent-foreground',
  ghost: 'hover:bg-accent hover:text-accent-foreground',
  destructive: 'bg-destructive text-white hover:bg-destructive/90',
}[props.variant]))
const sizeClass = computed(() => ({
  sm: 'h-8 px-3 text-xs',
  md: 'h-9 px-4 text-sm',
  icon: 'h-9 w-9',
}[props.size]))
</script>

<template>
  <button :type="type" :disabled="disabled" :class="cn(
    'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-medium transition-colors',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50',
    variantClass, sizeClass, $attrs.class as string)" v-bind="$attrs">
    <slot />
  </button>
</template>

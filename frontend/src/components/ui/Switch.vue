<script setup lang="ts">
import { cn } from '../../lib/utils'
const props = withDefaults(defineProps<{ modelValue?: boolean; disabled?: boolean }>(), { modelValue: false, disabled: false })
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
function toggle() {
  if (!props.disabled) emit('update:modelValue', !props.modelValue)
}
</script>
<template>
  <button type="button" role="switch" :aria-checked="modelValue" :disabled="disabled" @click="toggle"
    :class="cn('inline-flex h-5 w-9 shrink-0 items-center rounded-full border-2 border-transparent transition-colors',
      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50',
      modelValue ? 'bg-emerald-500' : 'bg-input')">
    <span :class="cn('pointer-events-none block h-4 w-4 rounded-full bg-background shadow-lg transition-transform',
      modelValue ? 'translate-x-4' : 'translate-x-0')" />
  </button>
</template>

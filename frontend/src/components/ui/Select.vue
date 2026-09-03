<script setup lang="ts">
defineProps<{ modelValue?: string; options: Array<{ value: string; label: string }>; disabled?: boolean }>()
defineEmits<{ 'update:modelValue': [value: string] }>()
</script>
<template>
  <select :value="modelValue" :disabled="disabled"
    class="flex h-9 w-full rounded-lg border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors
      focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"
    @change="$emit('update:modelValue', ($event.target as HTMLSelectElement).value)" v-bind="$attrs">
    <option v-for="option in options" :key="option.value" :value="option.value">{{ option.label }}</option>
  </select>
</template>

<script setup lang="ts">
defineProps<{ type?: string; modelValue?: string; placeholder?: string; disabled?: boolean }>()
defineEmits<{ 'update:modelValue': [value: string] }>()
</script>
<template>
  <input :type="type ?? 'text'" :value="modelValue" :placeholder="placeholder" :disabled="disabled"
    class="flex h-9 w-full rounded-lg border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors
      placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50
      disabled:cursor-not-allowed disabled:opacity-50"
    @input="$emit('update:modelValue', ($event.target as HTMLInputElement).value)" v-bind="$attrs" />
</template>

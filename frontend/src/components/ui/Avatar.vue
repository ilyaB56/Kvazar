<script setup lang="ts">
defineProps<{ initials?: string }>()
</script>
<template>
  <span class="relative flex h-8 w-8 shrink-0 overflow-hidden rounded-full">
    <span class="flex h-full w-full items-center justify-center rounded-full bg-emerald-100 text-xs font-bold text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
      {{ initials }}
    </span>
  </span>
</template>

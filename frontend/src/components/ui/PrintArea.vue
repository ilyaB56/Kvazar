<script setup lang="ts">
// Печатная зона (§4): на бумагу выводится только содержимое этого блока
// (механика data-erp-print из эталона, см. styles/tokens.css @media print).
</script>

<template>
  <div data-erp-print class="rounded-xl border border-border bg-card p-6 print:border-0 print:shadow-none">
    <slot />
  </div>
</template>

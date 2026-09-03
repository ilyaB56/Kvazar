<script setup lang="ts">
import { useToast } from './Toast'
import { cn } from '../../lib/utils'

const { state, dismiss } = useToast()
</script>

<template>
  <div class="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-80 flex-col gap-2" aria-live="polite">
    <div
      v-for="toast in state.items"
      :key="toast.id"
      role="status"
      :class="cn(
        'pointer-events-auto rounded-xl border p-4 shadow-lg transition-all erp-row-in',
        toast.tone === 'success'
          ? 'border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/60'
          : toast.tone === 'error'
            ? 'border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/60'
            : 'border-border bg-popover',
      )"
    >
      <div class="flex items-start justify-between gap-2">
        <div>
          <p class="text-sm font-semibold">{{ toast.title }}</p>
          <p v-if="toast.description" class="mt-0.5 text-sm text-muted-foreground">{{ toast.description }}</p>
        </div>
        <button type="button" class="text-muted-foreground hover:text-foreground" aria-label="×" @click="dismiss(toast.id)">×</button>
      </div>
    </div>
  </div>
</template>

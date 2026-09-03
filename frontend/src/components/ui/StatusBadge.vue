<script setup lang="ts">
import Badge from './Badge.vue'
import { cn } from '../../lib/utils'

export type StatusTone = 'sky' | 'amber' | 'teal' | 'emerald' | 'red' | 'zinc'

const props = defineProps<{ tone: StatusTone; label: string }>()

const tones: Record<StatusTone, string> = {
  sky: 'bg-sky-100 text-sky-700 border-sky-200 dark:bg-sky-950/60 dark:text-sky-300 dark:border-sky-900',
  amber: 'bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-950/60 dark:text-amber-300 dark:border-amber-900',
  teal: 'bg-teal-100 text-teal-700 border-teal-200 dark:bg-teal-950/60 dark:text-teal-300 dark:border-teal-900',
  emerald: 'bg-emerald-100 text-emerald-700 border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-900',
  red: 'bg-red-100 text-red-600 border-red-200 dark:bg-red-950/60 dark:text-red-400 dark:border-red-900',
  zinc: 'bg-zinc-100 text-zinc-600 border-zinc-200 dark:bg-zinc-800 dark:text-zinc-400 dark:border-zinc-700',
}
</script>
<template>
  <Badge variant="outline" :class="cn('font-medium', tones[tone])">{{ label }}</Badge>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '../../lib/utils'
import Card from './Card.vue'
import CardHeader from './CardHeader.vue'
import CardDescription from './CardDescription.vue'
import CardContent from './CardContent.vue'

const props = withDefaults(
  defineProps<{ title: string; value: string; delta?: number; invertDelta?: boolean }>(),
  { delta: undefined, invertDelta: false },
)
const positive = computed(() =>
  props.delta === undefined ? true : props.invertDelta ? props.delta < 0 : props.delta > 0,
)
</script>

<template>
  <Card>
    <CardHeader class="space-y-1">
      <CardDescription>{{ title }}</CardDescription>
      <div class="flex items-baseline gap-2">
        <span class="text-2xl font-bold tracking-tight tabular-nums">{{ value }}</span>
        <span v-if="delta !== undefined" :class="cn(
          'inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-xs font-semibold',
          positive
            ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300'
            : 'bg-red-100 text-red-600 dark:bg-red-950/60 dark:text-red-400',
        )">
          {{ delta > 0 ? '▲' : '▼' }} {{ Math.abs(delta).toFixed(1) }}%
        </span>
      </div>
    </CardHeader>
    <CardContent v-if="$slots.default" class="text-sm text-muted-foreground"><slot /></CardContent>
  </Card>
</template>

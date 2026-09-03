<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    points: number[]
    labels?: string[]
    height?: number
  }>(),
  { labels: () => [], height: 160 },
)

const width = 560
const pad = 8

function bars(points: number[], height: number) {
  const max = Math.max(...points, 1)
  const slot = (width - pad * 2) / Math.max(points.length, 1)
  const barWidth = Math.min(slot * 0.6, 48)
  return points.map((value, index) => ({
    x: pad + index * slot + (slot - barWidth) / 2,
    y: pad + (height - pad * 2) * (1 - value / max),
    width: barWidth,
    height: (height - pad * 2) * (value / max),
    value,
  }))
}
</script>

<template>
  <svg :viewBox="`0 0 ${width} ${height}`" class="w-full" role="img" preserveAspectRatio="none">
    <rect
      v-for="(bar, index) in bars(points, height)"
      :key="index"
      :x="bar.x"
      :y="bar.y"
      :width="bar.width"
      :height="Math.max(bar.height, 1)"
      rx="4"
      fill="#10b981"
      opacity="0.85"
    >
      <title v-if="labels[index]">{{ labels[index] }}: {{ bar.value }}</title>
    </rect>
  </svg>
</template>

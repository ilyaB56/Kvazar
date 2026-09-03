<script setup lang="ts">
// Мини-графики собственным SVG (§4: без библиотек, только два типа).
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

function geometry(points: number[], height: number) {
  const max = Math.max(...points, 1)
  const min = Math.min(...points, 0)
  const span = max - min || 1
  const step = points.length > 1 ? (width - pad * 2) / (points.length - 1) : 0
  const coords = points.map((value, index) => ({
    x: pad + index * step,
    y: pad + (height - pad * 2) * (1 - (value - min) / span),
  }))
  const line = coords.map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x},${point.y}`).join(' ')
  const area = `${line} L${coords[coords.length - 1]?.x ?? pad},${height - pad} L${pad},${height - pad} Z`
  return { coords, line, area }
}
</script>

<template>
  <svg :viewBox="`0 0 ${width} ${height}`" class="w-full" role="img" preserveAspectRatio="none">
    <defs>
      <linearGradient id="erp-line-fill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#10b981" stop-opacity="0.25" />
        <stop offset="100%" stop-color="#10b981" stop-opacity="0" />
      </linearGradient>
    </defs>
    <path :d="geometry(points, height).area" fill="url(#erp-line-fill)" />
    <path :d="geometry(points, height).line" fill="none" stroke="#10b981" stroke-width="2" stroke-linecap="round" />
    <circle
      v-for="(point, index) in geometry(points, height).coords"
      :key="index"
      :cx="point.x"
      :cy="point.y"
      r="3"
      fill="#10b981"
    >
      <title v-if="labels[index]">{{ labels[index] }}: {{ points[index] }}</title>
    </circle>
  </svg>
</template>

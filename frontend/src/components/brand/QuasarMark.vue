<script setup lang="ts">
// Векторный знак ERP «Квазар» — перенос 1:1 из components-erp/quasar-mark.tsx
// (макет v0.11): раскалённое ядро интеграционной платформы, орбита
// изолированных модулей и точка-спутник (подключаемая интеграция).
// Для печатных форм — статический brand/quasar-mark.svg (public/).
//
// Звёзды-модули (решение основателя 2026-09-06, спека §2): россыпь искр
// вокруг ядра и орбитального диска — символ модулей, подключённых к ERP
// (склад, закупки, продажи, CRM, ИИ, интеграции). Набор статичный;
// единственный изумрудный акцент среди светлых.
import { useId } from 'vue'

withDefaults(defineProps<{ class?: string }>(), { class: '' })

const uid = useId()
const gradId = `qg-${uid}`
const coreId = `qc-${uid}`

interface Star {
  x: number
  y: number
  scale: number
  fill: string
  opacity: number
}

// 4-лучевая искра единичного радиуса, позиция/размер — через transform;
// тонкий тёмный контур — чтобы светлые звёзды читались на белом фоне
// (favicon в светлой теме, печать mono-варианта)
const STAR_PATH = 'M0 -1 Q0.2 -0.2 1 0 Q0.2 0.2 0 1 Q-0.2 0.2 -1 0 Q-0.2 -0.2 0 -1 Z'
const stars: Star[] = [
  { x: 13.5, y: 12.5, scale: 2.4, fill: '#ffffff', opacity: 0.9 },
  { x: 41, y: 8, scale: 1.5, fill: '#ffffff', opacity: 0.75 },
  { x: 49.5, y: 40, scale: 1.4, fill: '#ffffff', opacity: 0.65 },
  { x: 44.5, y: 50.5, scale: 2, fill: '#ffffff', opacity: 0.8 },
  { x: 20.5, y: 47, scale: 1.6, fill: '#ffffff', opacity: 0.7 },
  { x: 8.5, y: 29.5, scale: 1.8, fill: '#34d399', opacity: 0.95 },
]
</script>

<template>
  <svg viewBox="0 0 64 64" fill="none" :class="$props.class" aria-hidden="true" focusable="false">
    <defs>
      <linearGradient :id="gradId" x1="10" y1="14" x2="54" y2="50" gradientUnits="userSpaceOnUse">
        <stop offset="0" stop-color="#c084fc" /><stop offset="1" stop-color="#7c3aed" />
      </linearGradient>
      <radialGradient :id="coreId" cx="0.4" cy="0.35" r="0.9">
        <stop offset="0" stop-color="#ffffff" /><stop offset="0.55" stop-color="#e9d5ff" /><stop offset="1" stop-color="#a855f7" />
      </radialGradient>
    </defs>
    <!-- звёзды-модули -->
    <path
      v-for="(star, i) in stars" :key="i" :d="STAR_PATH"
      :transform="`translate(${star.x} ${star.y}) scale(${star.scale})`"
      :fill="star.fill" :opacity="star.opacity"
      stroke="#0f0d17" stroke-opacity="0.3" stroke-width="0.12"
    />
    <!-- орбита модулей -->
    <ellipse cx="32" cy="32" rx="26" ry="11.5" :stroke="`url(#${gradId})`" stroke-width="3.5" stroke-linecap="round" transform="rotate(-18 32 32)" />
    <!-- точка-спутник: подключаемая интеграция -->
    <circle cx="53.5" cy="21.5" r="4" :fill="`url(#${gradId})`" />
    <!-- светящееся ядро платформы и ИИ -->
    <circle cx="32" cy="32" r="11" :fill="`url(#${coreId})`" />
    <circle cx="28.5" cy="28.5" r="3.2" fill="#ffffff" opacity="0.85" />
  </svg>
</template>

<script setup lang="ts">
// Векторный знак ERP «Квазар» — перенос 1:1 из components-erp/quasar-mark.tsx
// (макет v0.11): раскалённое ядро интеграционной платформы, орбита
// изолированных модулей и точка-спутник (подключаемая интеграция).
// Для печатных форм — статический brand/quasar-mark.svg (public/).
import { useId } from 'vue'

withDefaults(defineProps<{ class?: string }>(), { class: '' })

const uid = useId()
const gradId = `qg-${uid}`
const coreId = `qc-${uid}`
</script>

<template>
  <svg
    viewBox="0 0 64 64" fill="none" :class="$props.class"
    aria-hidden="true" focusable="false"
  >
    <defs>
      <linearGradient :id="gradId" x1="10" y1="14" x2="54" y2="50" gradientUnits="userSpaceOnUse">
        <stop offset="0" stop-color="#c084fc" />
        <stop offset="1" stop-color="#7c3aed" />
      </linearGradient>
      <radialGradient :id="coreId" cx="0.4" cy="0.35" r="0.9">
        <stop offset="0" stop-color="#ffffff" />
        <stop offset="0.55" stop-color="#e9d5ff" />
        <stop offset="1" stop-color="#a855f7" />
      </radialGradient>
    </defs>
    <!-- орбита модулей -->
    <ellipse
      cx="32" cy="32" rx="26" ry="11.5"
      :stroke="`url(#${gradId})`" stroke-width="3.5" stroke-linecap="round"
      transform="rotate(-18 32 32)"
    />
    <!-- точка-спутник: подключаемая интеграция -->
    <circle cx="53.5" cy="21.5" r="4" :fill="`url(#${gradId})`" />
    <!-- светящееся ядро платформы и ИИ -->
    <circle cx="32" cy="32" r="11" :fill="`url(#${coreId})`" />
    <circle cx="28.5" cy="28.5" r="3.2" fill="#ffffff" opacity="0.85" />
  </svg>
</template>

<script setup lang="ts">
// Дефолт p-6 pt-0 (shadn-паттерн: верхний отступ даёт CardHeader). Класс
// вызывающего кода имеет приоритет для паддингов: иначе p-5 с дашборда
// конфликтует с pt-0 по порядку CSS-правил и контент прилипает к верхней
// границе (багрепорт: KPI-карточки дашборда). Мини-merge только для группы
// padding-классов: если во внешнем классе есть любой p*/px/py/pt/pr/pb/pl,
// соответствующие дефолтные паддинги отбрасываются целиком.
import { computed, useAttrs } from 'vue'

defineOptions({ inheritAttrs: false })

const PADDING_RE = /^-?p[trblxy]?-/

const attrs = useAttrs()
const classes = computed(() => {
  const outer = typeof attrs.class === 'string' ? attrs.class : ''
  const hasOuterPadding = outer.split(/\s+/).some((cls) => PADDING_RE.test(cls))
  const base = hasOuterPadding
    ? 'p-6 pt-0'.split(/\s+/).filter((cls) => !PADDING_RE.test(cls))
    : ['p-6', 'pt-0']
  return [...base, outer].filter(Boolean).join(' ')
})
</script>

<template>
  <div :class="classes"><slot /></div>
</template>

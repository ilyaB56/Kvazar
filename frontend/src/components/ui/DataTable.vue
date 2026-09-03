<script setup lang="ts">
// DataTable (§4): сортировка, фильтр-строка, пагинация, анимация строк.
// Данные — пропсом (серверная загрузка остаётся на вызывающем);
// ячейки кастомизируются слотом #cell-<key> (fallback — значение строки).
import { computed, ref, useSlots } from 'vue'
import { useI18n } from 'vue-i18n'
import { cn } from '../../lib/utils'
import Input from './Input.vue'
import Button from './Button.vue'
import EmptyState from './EmptyState.vue'

export interface DataTableColumn {
  key: string
  label: string
  sortable?: boolean
  align?: 'left' | 'right'
}

const props = withDefaults(
  defineProps<{
    columns: DataTableColumn[]
    rows: Array<Record<string, unknown>>
    rowKey?: string
    pageSize?: number
  }>(),
  { rowKey: 'id', pageSize: 10 },
)
const { t } = useI18n()
const slots = useSlots()

const query = ref('')
const sortKey = ref('')
const sortDir = ref<'asc' | 'desc'>('asc')
const page = ref(1)

const stringOf = (value: unknown): string =>
  value === null || value === undefined ? '' : String(value)

const filtered = computed(() => {
  if (!query.value.trim()) return props.rows
  const needle = query.value.trim().toLowerCase()
  return props.rows.filter((row) =>
    props.columns.some((column) => stringOf(row[column.key]).toLowerCase().includes(needle)),
  )
})

const sorted = computed(() => {
  if (!sortKey.value) return filtered.value
  const key = sortKey.value
  const dir = sortDir.value === 'asc' ? 1 : -1
  return [...filtered.value].sort((a, b) =>
    stringOf(a[key]).localeCompare(stringOf(b[key]), 'ru', { numeric: true }) * dir,
  )
})

const pages = computed(() => Math.max(1, Math.ceil(sorted.value.length / props.pageSize)))
const pageRows = computed(() => {
  const current = Math.min(page.value, pages.value)
  return sorted.value.slice((current - 1) * props.pageSize, current * props.pageSize)
})

function toggleSort(column: DataTableColumn) {
  if (!column.sortable) return
  if (sortKey.value === column.key) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortKey.value = column.key
    sortDir.value = 'asc'
  }
}
</script>

<template>
  <div class="space-y-3">
    <div v-if="$slots.toolbar || true" class="flex flex-wrap items-center justify-between gap-2">
      <Input v-model="query" :placeholder="t('ui.searchPlaceholder')" class="max-w-xs" />
      <slot name="toolbar" />
    </div>

    <div class="overflow-hidden rounded-xl border border-border">
      <div class="overflow-x-auto erp-scroll">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-border bg-muted/50">
              <th
                v-for="column in columns"
                :key="column.key"
                :class="cn('px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground',
                  column.align === 'right' && 'text-right')"
                :aria-sort="sortKey === column.key ? (sortDir === 'asc' ? 'ascending' : 'descending') : undefined"
              >
                <button
                  v-if="column.sortable"
                  type="button"
                  class="inline-flex items-center gap-1 hover:text-foreground"
                  @click="toggleSort(column)"
                >
                  {{ column.label }}
                  <span class="text-[10px]">{{ sortKey === column.key ? (sortDir === 'asc' ? '▲' : '▼') : '↕' }}</span>
                </button>
                <template v-else>{{ column.label }}</template>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in pageRows"
              :key="stringOf(row[rowKey])"
              class="erp-row-in border-b border-border/60 transition-colors last:border-0 hover:bg-muted/40"
            >
              <td
                v-for="column in columns"
                :key="column.key"
                :class="cn('px-4 py-2.5', column.align === 'right' && 'text-right tabular-nums')"
              >
                <slot :name="`cell-${column.key}`" :row="row">
                  {{ stringOf(row[column.key]) }}
                </slot>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <EmptyState v-if="!pageRows.length" />

    <div v-if="pages > 1" class="flex items-center justify-between text-sm text-muted-foreground">
      <span>{{ t('ui.pageOf', { page: Math.min(page, pages), total: pages }) }}</span>
      <div class="flex gap-1">
        <Button variant="outline" size="sm" :disabled="page <= 1" @click="page--">←</Button>
        <Button variant="outline" size="sm" :disabled="page >= pages" @click="page++">→</Button>
      </div>
    </div>
  </div>
</template>

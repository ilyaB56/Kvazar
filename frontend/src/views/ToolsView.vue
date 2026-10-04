<script setup lang="ts">
// Инструменты (devtools-spec §11 п.1): браузер таблиц БД — этап A.
// Вкладка «Таблицы»: дерево модулей → таблицы, колонки/фильтры/сортировка,
// построчный просмотр (маскируемые значения — «***»), экспорт и пресеты.
// Права: table_browser (ro/rw); ro достаточно для всех операций этапа A.
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Download, FileSpreadsheet, Filter, Trash2, Wrench } from 'lucide-vue-next'
import { get, post, del } from '../api/client'
import { useAuthStore } from '../stores/auth'
import {
  Badge, Button, Card, CardContent, Dialog, EmptyState, Input, Label,
  PaginatedList, Select, Skeleton, Tabs, useToast,
} from '../components/ui'
import type { PageOf } from '../components/ui'

const { t } = useI18n()
const auth = useAuthStore()
const toast = useToast()

// ---------- Права: без table_browser — пустое состояние ----------
const allowed = computed(() => auth.moduleLevel('table_browser') !== 'none')

// ---------- Типы контракта ----------
interface TableSummary {
  schema: string
  table: string
  module: string
  title: string
  company_scoped: boolean
  platform_only: boolean
  masked: string[]
}
interface ColumnInfo {
  name: string
  type: string
  nullable: boolean
  pk?: boolean
  fk?: boolean
}
interface TableDetail extends TableSummary {
  columns: ColumnInfo[]
}
type FilterOp = 'eq' | 'ne' | 'contains' | 'in' | 'between' | 'is_null' | 'not_null'
interface FilterPayload { col: string; op: FilterOp; value?: unknown }
interface FilterRow { col: string; op: FilterOp; value: string; value2: string }
interface Preset {
  id: string
  name: string
  definition: { filters: FilterPayload[]; sort: string[] | string; columns?: string[] }
  updated_at: string
}

// ---------- Список таблиц (левая панель) ----------
const tables = ref<TableSummary[] | null>(null)
const search = ref('')
const selectedKey = ref('')

const filteredTables = computed(() => {
  const list = tables.value ?? []
  const q = search.value.trim().toLowerCase()
  if (!q) return list
  return list.filter(
    (item) => item.table.toLowerCase().includes(q) || item.title.toLowerCase().includes(q),
  )
})
const groupedTables = computed(() => {
  const groups = new Map<string, TableSummary[]>()
  for (const item of filteredTables.value) {
    const list = groups.get(item.module) ?? []
    list.push(item)
    groups.set(item.module, list)
  }
  return [...groups.entries()].map(([module, items]) => ({ module, items }))
})

// ---------- Выбранная таблица ----------
const detail = ref<TableDetail | null>(null)
const detailLoading = ref(false)
const selected = computed(() =>
  tables.value?.find((item) => `${item.schema}.${item.table}` === selectedKey.value) ?? null)

async function selectTable(item: TableSummary) {
  selectedKey.value = `${item.schema}.${item.table}`
}

watch(selectedKey, async (key) => {
  if (!key || !selected.value) { detail.value = null; return }
  detailLoading.value = true
  filterRows.value = []
  appliedFilters.value = []
  sort1Col.value = ''
  sort1Dir.value = 'asc'
  sort2Col.value = ''
  sort2Dir.value = 'asc'
  appliedSort.value = ''
  presets.value = []
  presetOpen.value = false
  try {
    const [d, p] = await Promise.all([
      get<TableDetail>(`/system/tables/${selected.value!.schema}/${selected.value!.table}`),
      get<Preset[]>(`/system/tables/${selected.value!.schema}/${selected.value!.table}/presets`),
    ])
    detail.value = d
    selectedColumns.value = d.columns.map((c) => c.name)
    presets.value = p
  } catch (error) {
    toast.apiError(error)
  } finally {
    detailLoading.value = false
  }
})

// ---------- Колонки ----------
const columnsOpen = ref(false)
const selectedColumns = ref<string[]>([])
const visibleColumns = computed(() => {
  if (!detail.value) return []
  const masked = new Set(detail.value.masked)
  // маскируемые всегда в выборке: выключить нельзя
  return detail.value.columns
    .map((c) => c.name)
    .filter((name) => selectedColumns.value.includes(name) || masked.has(name))
})
function isMasked(name: string): boolean {
  return !!detail.value?.masked.includes(name)
}

// ---------- Фильтры ----------
const filterRows = ref<FilterRow[]>([])
const appliedFilters = ref<FilterPayload[]>([])

const OPS_TEXT: FilterOp[] = ['eq', 'ne', 'contains', 'in', 'is_null', 'not_null']
const OPS_ALL: FilterOp[] = ['eq', 'ne', 'contains', 'in', 'between', 'is_null', 'not_null']
function opsForColumn(col: string): FilterOp[] {
  const type = detail.value?.columns.find((c) => c.name === col)?.type ?? ''
  return /char|text|uuid/i.test(type) ? OPS_TEXT : OPS_ALL
}
function opNeedsValue(op: FilterOp): boolean {
  return op !== 'is_null' && op !== 'not_null'
}
function opNeedsTwo(op: FilterOp): boolean {
  return op === 'between'
}
function addFilter() {
  const first = detail.value?.columns[0]?.name
  if (!first) return
  filterRows.value.push({ col: first, op: 'eq', value: '', value2: '' })
}
function removeFilter(index: number) {
  filterRows.value.splice(index, 1)
}
function buildFilters(): FilterPayload[] {
  const result: FilterPayload[] = []
  for (const row of filterRows.value) {
    if (!opNeedsValue(row.op)) {
      result.push({ col: row.col, op: row.op })
    } else if (row.op === 'in') {
      const parts = row.value.split(',').map((s) => s.trim()).filter(Boolean)
      if (parts.length) result.push({ col: row.col, op: row.op, value: parts })
    } else if (row.op === 'between') {
      if (row.value !== '' && row.value2 !== '') {
        result.push({ col: row.col, op: row.op, value: [row.value, row.value2] })
      }
    } else if (row.value !== '') {
      result.push({ col: row.col, op: row.op, value: row.value })
    }
  }
  return result
}

// ---------- Сортировка ----------
const sort1Col = ref('')
const sort1Dir = ref<'asc' | 'desc'>('asc')
const sort2Col = ref('')
const sort2Dir = ref<'asc' | 'desc'>('asc')
const appliedSort = ref('')
function buildSort(): string {
  const parts: string[] = []
  if (sort1Col.value) parts.push(`${sort1Col.value},${sort1Dir.value}`)
  if (sort2Col.value && sort2Col.value !== sort1Col.value) {
    parts.push(`${sort2Col.value},${sort2Dir.value}`)
  }
  return parts.join('|')
}

// ---------- Применение / сброс ----------
function apply() {
  appliedFilters.value = buildFilters()
  appliedSort.value = buildSort()
  toast.success(t('tools.applied'))
}
function reset() {
  filterRows.value = []
  appliedFilters.value = []
  sort1Col.value = ''
  sort2Col.value = ''
  appliedSort.value = ''
  if (detail.value) selectedColumns.value = detail.value.columns.map((c) => c.name)
}

// resetKey PaginatedList: таблица + применённые фильтры/сортировка/колонки
const resetKey = computed(() => JSON.stringify([
  selectedKey.value, appliedFilters.value, appliedSort.value, visibleColumns.value,
]))

// ---------- Строки ----------
type Row = Record<string, unknown>
async function fetchRows(offset: number, limit: number): Promise<PageOf<Row>> {
  const s = selected.value
  if (!s) return { items: [], total: 0 }
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (visibleColumns.value.length) params.set('columns', visibleColumns.value.join(','))
  if (appliedFilters.value.length) params.set('filters', JSON.stringify(appliedFilters.value))
  if (appliedSort.value) params.set('sort', appliedSort.value)
  return get<PageOf<Row>>(`/system/tables/${s.schema}/${s.table}/rows?${params.toString()}`)
}

// отображение ячейки: null — тире, маскируемые — ***, длинные — сокращённо
function cellText(value: unknown): string {
  if (value === null || value === undefined) return '—'
  const text = String(value)
  if (text.length > 20) return `${text.slice(0, 10)}…${text.slice(-6)}`
  return text
}

// ---------- Экспорт (бинарный — сырой fetch, api/client парсит JSON) ----------
const exporting = ref(false)
async function exportRows(fmt: 'csv' | 'xlsx') {
  const s = selected.value
  if (!s || exporting.value) return
  exporting.value = true
  try {
    const response = await fetch(
      `/api/v1/system/tables/${s.schema}/${s.table}/export`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${auth.accessToken}`,
        },
        body: JSON.stringify({
          columns: visibleColumns.value,
          filters: appliedFilters.value,
          sort: appliedSort.value,
          fmt,
        }),
      },
    )
    if (response.ok) {
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${s.table}.${fmt}`
      link.click()
      URL.revokeObjectURL(url)
      return
    }
    // 422 export_limit и прочие ошибки: detail из тела — в тост
    let message = `HTTP ${response.status}`
    try {
      const data = await response.json() as { detail?: unknown }
      if (typeof data.detail === 'string') message = data.detail
      else if (data.detail !== undefined) message = JSON.stringify(data.detail)
    } catch { /* не JSON — остаётся код статуса */ }
    toast.apiError(new Error(message))
  } catch (error) {
    toast.apiError(error)
  } finally {
    exporting.value = false
  }
}

// ---------- Пресеты ----------
const presets = ref<Preset[]>([])
const presetOpen = ref(false)
const presetName = ref('')
const presetSaving = ref(false)
async function savePreset() {
  const s = selected.value
  if (!s || !presetName.value.trim() || presetSaving.value) return
  presetSaving.value = true
  try {
    await post(
      `/system/tables/${s.schema}/${s.table}/presets?name=${encodeURIComponent(presetName.value.trim())}`,
      { definition: { filters: appliedFilters.value, sort: buildSort(), columns: visibleColumns.value } },
    )
    presetName.value = ''
    presetOpen.value = false
    presets.value = await get<Preset[]>(`/system/tables/${s.schema}/${s.table}/presets`)
    toast.success(t('tools.presetSaved'))
  } catch (error) {
    toast.apiError(error)
  } finally {
    presetSaving.value = false
  }
}
function applyPreset(preset: Preset) {
  const definition = preset.definition ?? { filters: [], sort: [] }
  filterRows.value = (definition.filters ?? []).map((f) => ({
    col: f.col,
    op: f.op,
    value: Array.isArray(f.value) ? f.value.join(', ') : f.value === undefined ? '' : String(f.value),
    value2: Array.isArray(f.value) && f.value.length > 1 ? String(f.value[1]) : '',
  }))
  const sortParts = Array.isArray(definition.sort)
    ? definition.sort
    : typeof definition.sort === 'string' && definition.sort
      ? definition.sort.split('|')
      : []
  const [p1, p2] = sortParts.map((part) => part.split(','))
  sort1Col.value = p1?.[0] ?? ''
  sort1Dir.value = (p1?.[1] as 'asc' | 'desc') ?? 'asc'
  sort2Col.value = p2?.[0] ?? ''
  sort2Dir.value = (p2?.[1] as 'asc' | 'desc') ?? 'asc'
  if (definition.columns?.length && detail.value) {
    selectedColumns.value = detail.value.columns
      .map((c) => c.name)
      .filter((name) => definition.columns!.includes(name) || isMasked(name))
  }
  apply()
}
async function deletePreset(id: string) {
  const s = selected.value
  if (!s) return
  try {
    await del(`/system/tables/${s.schema}/${s.table}/presets/${id}`)
    presets.value = presets.value.filter((p) => p.id !== id)
    toast.success(t('tools.presetDeleted'))
  } catch (error) {
    toast.apiError(error)
  }
}

// ---------- Загрузка списка таблиц ----------
async function loadTables() {
  try {
    tables.value = await get<TableSummary[]>('/system/tables')
  } catch (error) {
    tables.value = []
    toast.apiError(error)
  }
}
if (allowed.value) void loadTables()

// ---------- Вкладки (этап A — только «Таблицы») ----------
const activeTab = ref('tables')
const tabs = [{ key: 'tables', label: t('tools.tablesTab') }]

const columnOptions = computed(() =>
  (detail.value?.columns ?? []).map((c) => ({ value: c.name, label: c.name })))
const sortOptions = computed(() => [
  { value: '', label: t('tools.sortNone') },
  ...columnOptions.value,
])
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-xl font-bold tracking-tight text-foreground">{{ t('tools.title') }}</h1>
        <p class="text-sm text-muted-foreground">{{ t('tools.subtitle') }}</p>
      </div>
      <Tabs v-model="activeTab" :tabs="tabs" />
    </div>

    <EmptyState
      v-if="!allowed"
      :title="t('tools.noAccessTitle')"
      :description="t('tools.noAccessDescription')"
    />

    <div v-else class="grid gap-4 lg:grid-cols-[300px_1fr]">
      <!-- Левая панель: модуль → таблицы -->
      <Card class="flex max-h-[75vh] flex-col">
        <CardContent class="flex min-h-0 flex-1 flex-col gap-3 p-4">
          <Input v-model="search" type="text" :placeholder="t('tools.searchTables')" />
          <div v-if="tables === null" class="space-y-2">
            <Skeleton v-for="i in 6" :key="i" class="h-8 w-full" />
          </div>
          <div v-else-if="groupedTables.length === 0" class="text-sm text-muted-foreground">
            {{ t('tools.noTables') }}
          </div>
          <nav v-else class="min-h-0 flex-1 space-y-3 overflow-y-auto erp-scroll" :aria-label="t('tools.tablesTab')">
            <div v-for="group in groupedTables" :key="group.module">
              <p class="mb-1 px-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                {{ group.module }}
              </p>
              <button
                v-for="item in group.items"
                :key="`${item.schema}.${item.table}`"
                type="button"
                class="block w-full rounded-lg px-2.5 py-1.5 text-left text-sm transition-colors"
                :class="selectedKey === `${item.schema}.${item.table}`
                  ? 'bg-primary/10 font-medium text-primary'
                  : 'text-foreground/80 hover:bg-muted'"
                :title="item.title"
                @click="selectTable(item)"
              >
                <span class="block truncate">{{ item.title }}</span>
                <span class="block truncate font-mono text-[11px] text-muted-foreground">
                  {{ item.schema }}.{{ item.table }}
                </span>
              </button>
            </div>
          </nav>
        </CardContent>
      </Card>

      <!-- Правая панель -->
      <div class="min-w-0 space-y-4">
        <EmptyState
          v-if="!selected"
          :title="t('tools.selectTable')"
          :description="t('tools.selectTableDescription')"
        >
          <Wrench class="h-8 w-8 text-muted-foreground/50" />
        </EmptyState>

        <template v-else>
          <!-- Заголовок таблицы -->
          <Card>
            <CardContent class="space-y-1 p-4">
              <div class="flex flex-wrap items-center gap-2">
                <h2 class="text-base font-semibold text-foreground">{{ detail?.title ?? selected.title }}</h2>
                <Badge v-if="detail?.company_scoped ?? selected.company_scoped" variant="secondary">
                  {{ t('tools.companyScoped') }}
                </Badge>
                <Badge v-if="detail?.platform_only ?? selected.platform_only" variant="outline">
                  {{ t('tools.platformOnly') }}
                </Badge>
              </div>
              <p class="font-mono text-xs text-muted-foreground">
                {{ selected.schema }}.{{ selected.table }}
              </p>
            </CardContent>
          </Card>

          <div v-if="detailLoading" class="space-y-3">
            <Skeleton class="h-24 w-full" />
            <Skeleton class="h-64 w-full" />
          </div>

          <template v-else-if="detail">
            <!-- Колонки -->
            <Card>
              <CardContent class="p-4">
                <button
                  type="button"
                  class="flex w-full items-center justify-between text-sm font-semibold text-foreground"
                  @click="columnsOpen = !columnsOpen"
                >
                  {{ t('tools.columns') }} ({{ detail.columns.length }})
                  <span class="text-xs font-normal text-muted-foreground">
                    {{ columnsOpen ? '▲' : '▼' }}
                  </span>
                </button>
                <div v-if="columnsOpen" class="mt-3 grid gap-x-4 gap-y-1.5 sm:grid-cols-2 xl:grid-cols-3">
                  <label
                    v-for="column in detail.columns"
                    :key="column.name"
                    class="flex items-center gap-2 rounded px-1 py-0.5 text-sm"
                    :class="{ 'text-muted-foreground': isMasked(column.name) }"
                  >
                    <input
                      type="checkbox"
                      class="h-4 w-4 accent-primary"
                      :checked="selectedColumns.includes(column.name) || isMasked(column.name)"
                      :disabled="isMasked(column.name)"
                      @change="selectedColumns = selectedColumns.includes(column.name)
                        ? selectedColumns.filter((name) => name !== column.name)
                        : [...selectedColumns, column.name]"
                    >
                    <span class="min-w-0 flex-1 truncate font-mono text-xs" :title="`${column.name} ${column.type}`">
                      {{ column.name }}
                    </span>
                    <span class="shrink-0 text-[10px] text-muted-foreground">{{ column.type }}</span>
                    <Badge v-if="isMasked(column.name)" variant="secondary" class="ml-1">
                      {{ t('tools.secret') }}
                    </Badge>
                    <span v-if="column.pk" class="shrink-0 text-[10px] font-bold text-amber-500">PK</span>
                  </label>
                </div>
              </CardContent>
            </Card>

            <!-- Фильтры + сортировка -->
            <Card>
              <CardContent class="space-y-4 p-4">
                <div class="space-y-2">
                  <div class="flex items-center justify-between">
                    <p class="flex items-center gap-1.5 text-sm font-semibold text-foreground">
                      <Filter class="h-4 w-4" /> {{ t('tools.filters') }}
                    </p>
                    <Button variant="outline" size="sm" @click="addFilter">{{ t('tools.addFilter') }}</Button>
                  </div>
                  <p v-if="filterRows.length === 0" class="text-xs text-muted-foreground">
                    {{ t('tools.noFilters') }}
                  </p>
                  <div
                    v-for="(row, index) in filterRows"
                    :key="index"
                    class="flex flex-wrap items-center gap-2"
                  >
                    <Select
                      v-model="row.col"
                      :options="columnOptions"
                      class="w-44 shrink-0"
                    />
                    <Select
                      v-model="row.op"
                      :options="opsForColumn(row.col).map((op) => ({ value: op, label: t(`tools.ops.${op}`) }))"
                      class="w-36 shrink-0"
                    />
                    <Input
                      v-if="opNeedsValue(row.op) && !opNeedsTwo(row.op)"
                      v-model="row.value"
                      type="text"
                      class="h-9 w-44"
                      :placeholder="t('tools.value')"
                    />
                    <template v-if="opNeedsTwo(row.op)">
                      <Input v-model="row.value" type="text" class="h-9 w-32" :placeholder="t('tools.valueFrom')" />
                      <Input v-model="row.value2" type="text" class="h-9 w-32" :placeholder="t('tools.valueTo')" />
                    </template>
                    <Button variant="ghost" size="icon" class="h-9 w-9 shrink-0" :aria-label="t('tools.removeFilter')" @click="removeFilter(index)">
                      <Trash2 class="h-4 w-4" />
                    </Button>
                  </div>
                </div>

                <div class="space-y-2 border-t border-border pt-3">
                  <p class="text-sm font-semibold text-foreground">{{ t('tools.sort') }}</p>
                  <div class="flex flex-wrap items-center gap-2">
                    <Select v-model="sort1Col" :options="sortOptions" class="w-44" />
                    <Select
                      v-if="sort1Col"
                      v-model="sort1Dir"
                      :options="[{ value: 'asc', label: t('tools.asc') }, { value: 'desc', label: t('tools.desc') }]"
                      class="w-28"
                    />
                    <Select v-model="sort2Col" :options="sortOptions" :disabled="!sort1Col" class="w-44" />
                    <Select
                      v-if="sort2Col"
                      v-model="sort2Dir"
                      :options="[{ value: 'asc', label: t('tools.asc') }, { value: 'desc', label: t('tools.desc') }]"
                      class="w-28"
                    />
                  </div>
                </div>

                <div class="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
                  <div class="flex gap-2">
                    <Button variant="emerald" size="sm" @click="apply">{{ t('tools.apply') }}</Button>
                    <Button variant="outline" size="sm" @click="reset">{{ t('tools.reset') }}</Button>
                  </div>
                  <div class="flex flex-wrap gap-2">
                    <Button variant="outline" size="sm" :disabled="exporting" @click="exportRows('csv')">
                      <Download class="h-4 w-4" /> {{ t('tools.exportCsv') }}
                    </Button>
                    <Button variant="outline" size="sm" :disabled="exporting" @click="exportRows('xlsx')">
                      <FileSpreadsheet class="h-4 w-4" /> {{ t('tools.exportXlsx') }}
                    </Button>
                    <Button variant="outline" size="sm" @click="presetOpen = true">
                      {{ t('tools.savePreset') }}
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>

            <!-- Пресеты -->
            <Card v-if="presets.length">
              <CardContent class="flex flex-wrap items-center gap-2 p-4">
                <p class="text-sm font-semibold text-foreground">{{ t('tools.presets') }}:</p>
                <div
                  v-for="preset in presets"
                  :key="preset.id"
                  class="flex items-center gap-1 rounded-full border border-border bg-muted px-2.5 py-0.5 text-xs"
                >
                  <button type="button" class="font-medium text-foreground hover:text-primary" @click="applyPreset(preset)">
                    {{ preset.name }}
                  </button>
                  <button
                    type="button"
                    class="text-muted-foreground hover:text-red-500"
                    :aria-label="t('tools.deletePreset')"
                    @click="deletePreset(preset.id)"
                  >
                    <Trash2 class="h-3.5 w-3.5" />
                  </button>
                </div>
              </CardContent>
            </Card>

            <!-- Строки -->
            <Card>
              <PaginatedList
                :fetch-page="fetchRows"
                :reset-key="resetKey"
                :page-size="50"
                bar-class="px-4"
              >
                <template #default="{ items, loading, total }">
                  <div v-if="loading" class="space-y-2 p-4">
                    <Skeleton v-for="i in 8" :key="i" class="h-8 w-full" />
                  </div>
                  <EmptyState
                    v-else-if="items.length === 0"
                    :title="t('tools.emptyRows')"
                    :description="total === 0 ? t('tools.emptyRowsDescription') : ''"
                  />
                  <div v-else class="overflow-x-auto">
                    <table class="w-full text-sm">
                      <thead>
                        <tr class="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                          <th
                            v-for="column in detail.columns.filter((c) => visibleColumns.includes(c.name))"
                            :key="column.name"
                            class="whitespace-nowrap px-3 py-2 font-semibold"
                          >
                            {{ column.name }}
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr
                          v-for="(row, index) in items"
                          :key="index"
                          class="border-b border-border/60 last:border-0 hover:bg-muted/50"
                        >
                          <td
                            v-for="column in detail.columns.filter((c) => visibleColumns.includes(c.name))"
                            :key="column.name"
                            class="max-w-[220px] truncate px-3 py-1.5 font-mono text-xs"
                            :class="isMasked(column.name) ? 'text-muted-foreground' : 'text-foreground'"
                            :title="row[column.name] === null || row[column.name] === undefined ? '' : String(row[column.name])"
                          >
                            <template v-if="isMasked(column.name)">***</template>
                            <template v-else>{{ cellText(row[column.name]) }}</template>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </template>
              </PaginatedList>
            </Card>
          </template>
        </template>
      </div>
    </div>

    <!-- Диалог сохранения пресета -->
    <Dialog
      :open="presetOpen"
      :title="t('tools.savePreset')"
      width="420px"
      @update:open="(v: boolean) => { if (!v) presetOpen = false }"
    >
      <form class="space-y-4" @submit.prevent="savePreset">
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('tools.presetName') }}</Label>
          <Input v-model="presetName" type="text" autofocus :placeholder="t('tools.presetNamePlaceholder')" />
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" type="button" @click="presetOpen = false">
            {{ t('ui.cancel') }}
          </Button>
          <Button variant="emerald" size="sm" type="submit" :disabled="presetSaving || !presetName.trim()">
            {{ t('tools.savePreset') }}
          </Button>
        </div>
      </form>
    </Dialog>
  </div>
</template>

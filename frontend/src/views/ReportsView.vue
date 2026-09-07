<script setup lang="ts">
// Отчёты (этап F редизайна, стр. 10 макета): ДДС по категориям за период
// (таблица + бар из cashflow-report), воронка CRM (этапы × суммы из
// pipeline-report, взвешенный прогноз), экспорт 1CClientBankExchange —
// кнопка скачивает файл с сервера (account_id + период). Блоки по правам:
// accounting / crm; пустые данные — EmptyState.
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Download } from 'lucide-vue-next'
import { get } from '../api/client'
import { Badge, Button, Card, CardContent, EmptyState, Label, Select, Skeleton, useToast } from '../components/ui'
import { useAuthStore } from '../stores/auth'
import { formatMoney2 } from '../utils/money'

const { t } = useI18n()
const auth = useAuthStore()
const toast = useToast()

const canAccounting = computed(() => auth.moduleLevel('accounting') !== 'none')
const canCrm = computed(() => auth.moduleLevel('crm') !== 'none')

// ---------- период ----------
// локальный ISO-день: toISOString уползает на сутки назад при UTC+N
function localDay(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function monthBounds(offsetMonths: number): { from: string; to: string } {
  const now = new Date()
  const from = new Date(now.getFullYear(), now.getMonth() + offsetMonths, 1)
  const to = offsetMonths === 0 ? now : new Date(now.getFullYear(), now.getMonth() + offsetMonths + 1, 0)
  return { from: localDay(from), to: localDay(to) }
}
const period = reactive({ from: monthBounds(0).from, to: monthBounds(0).to })

// ---------- ДДС по категориям ----------
interface CashflowTotal {
  kind: string
  category_id: string | null
  total: string
}
interface Category {
  id: string
  name: string
  kind: string
}
interface Account {
  id: string
  name: string
  currency: string
}

const loadingCashflow = ref(true)
const totals = ref<Array<CashflowTotal & { name: string }>>([])
const categories = ref<Category[]>([])
const accounts = ref<Account[]>([])

const incomeRows = computed(() => totals.value.filter((row) => row.kind === 'income'))
const expenseRows = computed(() => totals.value.filter((row) => row.kind === 'expense'))
const incomeSum = computed(() => incomeRows.value.reduce((s, r) => s + Number(r.total), 0))
const expenseSum = computed(() => expenseRows.value.reduce((s, r) => s + Math.abs(Number(r.total)), 0))
const maxAbs = computed(() =>
  Math.max(1, ...totals.value.map((row) => Math.abs(Number(row.total)))))

// ---------- Воронка ----------
interface PipelineStage {
  stage: string
  probability: number
  count: number
  total: number
  weighted: number
}
const loadingPipeline = ref(true)
const stages = ref<PipelineStage[]>([])
const openStages = computed(() => stages.value.filter((s) => s.probability > 0 && s.probability < 100))
const maxTotal = computed(() => Math.max(1, ...stages.value.map((s) => s.total)))
const weightedTotal = computed(() => openStages.value.reduce((s, r) => s + r.weighted, 0))

async function load() {
  if (canAccounting.value) {
    loadingCashflow.value = true
    try {
      const [cash, cats, accs] = await Promise.all([
        get<{ totals: CashflowTotal[] }>(
          `/accounting/report/cashflow?date_from=${period.from}&date_to=${period.to}`),
        get<Category[]>('/accounting/categories'),
        get<Account[]>('/accounting/accounts'),
      ])
      const catName = new Map(cats.map((c) => [c.id, c.name]))
      categories.value = cats
      accounts.value = accs
      totals.value = cash.totals.map((row) => ({
        ...row,
        name: row.category_id ? (catName.get(row.category_id) ?? '…') : t('reports.noCategory'),
      }))
    } catch {
      totals.value = []
    } finally {
      loadingCashflow.value = false
    }
  }
  if (canCrm.value) {
    loadingPipeline.value = true
    try {
      stages.value = (await get<{ stages: PipelineStage[] }>('/crm/report/pipeline')).stages
    } catch {
      stages.value = []
    } finally {
      loadingPipeline.value = false
    }
  }
}
onMounted(load)

function setMonth(offset: number) {
  const bounds = monthBounds(offset)
  period.from = bounds.from
  period.to = bounds.to
  void load()
}

// ---------- Экспорт 1C ----------
const exportForm = reactive({ accountId: '', from: monthBounds(0).from, to: monthBounds(0).to })
const exporting = ref(false)

async function export1c() {
  if (exporting.value || !exportForm.accountId) return
  exporting.value = true
  try {
    // файл (cp1251) — сырой fetch с Bearer: общий клиент парсит JSON
    const query = `date_from=${exportForm.from}&date_to=${exportForm.to}&account_id=${exportForm.accountId}`
    const response = await fetch(`/api/v1/accounting/export/client-bank?${query}`, {
      headers: { Authorization: `Bearer ${auth.accessToken}` },
    })
    if (!response.ok) {
      // 422 «нет проведённых транзакций за период» и т.п. — показываем detail
      let message = `HTTP ${response.status}`
      try {
        const data: unknown = await response.json()
        if (data && typeof data === 'object' && 'detail' in data) {
          message = String((data as { detail: unknown }).detail)
        }
      } catch { /* тело не JSON */ }
      throw new Error(message)
    }
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `1c-exchange-${exportForm.from.replaceAll('-', '')}.txt`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
    toast.success(t('reports.exported'))
  } catch (error) {
    toast.apiError(error)
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div>
        <h2 class="text-lg font-bold tracking-tight">{{ t('reports.title') }}</h2>
        <p class="text-sm text-muted-foreground">{{ t('reports.subtitle') }}</p>
      </div>
      <div v-if="canAccounting" class="flex items-center gap-2">
        <Button variant="outline" size="sm" @click="setMonth(-1)">← {{ t('reports.prevMonth') }}</Button>
        <Button variant="outline" size="sm" @click="setMonth(0)">{{ t('reports.thisMonth') }}</Button>
      </div>
    </div>

    <!-- ДДС по категориям -->
    <Card v-if="canAccounting" class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-5">
        <div class="flex flex-wrap items-baseline justify-between gap-2">
          <p class="text-base font-semibold">{{ t('reports.cashflowTitle') }}</p>
          <p class="text-xs text-muted-foreground">{{ period.from }} — {{ period.to }}</p>
        </div>

        <div v-if="loadingCashflow" class="mt-3 space-y-2">
          <Skeleton class="h-8 w-full" />
          <Skeleton class="h-8 w-full" />
          <Skeleton class="h-8 w-2/3" />
        </div>
        <div v-else-if="totals.length === 0" class="py-10">
          <EmptyState :title="t('ui.emptyTitle')" :description="t('reports.noCashflow')" />
        </div>
        <div v-else class="mt-3 grid grid-cols-1 gap-5 lg:grid-cols-2">
          <!-- Таблица -->
          <div class="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
            <table class="w-full text-sm">
              <thead>
                <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                  <th class="px-3 py-2 font-medium">{{ t('reports.colCategory') }}</th>
                  <th class="px-3 py-2 font-medium">{{ t('reports.colKind') }}</th>
                  <th class="px-3 py-2 text-right font-medium">{{ t('reports.colTotal') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in incomeRows" :key="`in-${row.category_id}`" class="border-t border-zinc-100 dark:border-zinc-800/70">
                  <td class="px-3 py-2 font-medium">{{ row.name }}</td>
                  <td class="px-3 py-2">{{ t('finance.kind.income') }}</td>
                  <td class="whitespace-nowrap px-3 py-2 text-right font-semibold text-emerald-600 dark:text-emerald-400">
                    {{ formatMoney2(row.total) }}
                  </td>
                </tr>
                <tr class="border-t border-zinc-200 bg-zinc-50/60 font-semibold dark:border-zinc-800 dark:bg-zinc-900/40">
                  <td class="px-3 py-2" colspan="2">{{ t('reports.incomeTotal') }}</td>
                  <td class="whitespace-nowrap px-3 py-2 text-right text-emerald-700 dark:text-emerald-300">
                    {{ formatMoney2(incomeSum.toFixed(2)) }}
                  </td>
                </tr>
                <tr v-for="row in expenseRows" :key="`out-${row.category_id}`" class="border-t border-zinc-100 dark:border-zinc-800/70">
                  <td class="px-3 py-2 font-medium">{{ row.name }}</td>
                  <td class="px-3 py-2">{{ t('finance.kind.expense') }}</td>
                  <td class="whitespace-nowrap px-3 py-2 text-right font-semibold">
                    {{ formatMoney2((Number(row.total) < 0 ? row.total : `-${row.total}`)) }}
                  </td>
                </tr>
                <tr class="border-t border-zinc-200 bg-zinc-50/60 font-semibold dark:border-zinc-800 dark:bg-zinc-900/40">
                  <td class="px-3 py-2" colspan="2">{{ t('reports.expenseTotal') }}</td>
                  <td class="whitespace-nowrap px-3 py-2 text-right">−{{ formatMoney2(expenseSum.toFixed(2)) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- Бар по категориям -->
          <div class="space-y-2.5 self-start">
            <div v-for="row in totals" :key="row.category_id ?? row.kind" class="flex items-center gap-2">
              <span class="w-32 truncate text-xs text-muted-foreground">{{ row.name }}</span>
              <div class="h-2.5 flex-1 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800">
                <div
                  class="h-full rounded-full"
                  :class="row.kind === 'income' ? 'bg-emerald-500' : 'bg-amber-500'"
                  :style="{ width: `${Math.max((Math.abs(Number(row.total)) / maxAbs) * 100, 1)}%` }"
                />
              </div>
              <span class="w-24 shrink-0 text-right text-xs font-medium tabular-nums">
                {{ formatMoney2(row.total) }}
              </span>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>

    <!-- Воронка CRM -->
    <Card v-if="canCrm" class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-5">
        <div class="flex flex-wrap items-baseline justify-between gap-2">
          <p class="text-base font-semibold">{{ t('reports.funnelTitle') }}</p>
          <p class="text-xs text-muted-foreground">
            {{ t('reports.weightedTotal') }}: <span class="font-semibold text-foreground">{{ formatMoney2(weightedTotal.toFixed(2)) }}</span>
          </p>
        </div>
        <div v-if="loadingPipeline" class="mt-3 space-y-2">
          <Skeleton class="h-8 w-full" />
          <Skeleton class="h-8 w-3/4" />
        </div>
        <div v-else-if="stages.length === 0" class="py-10">
          <EmptyState :title="t('ui.emptyTitle')" :description="t('reports.noFunnel')" />
        </div>
        <div v-else class="mt-3 space-y-3">
          <div v-for="stage in stages" :key="stage.stage" class="flex items-center gap-3">
            <span class="w-28 shrink-0 truncate text-xs">
              {{ stage.stage }}
              <Badge variant="secondary" class="ml-1 px-1 text-[10px]">{{ stage.probability }}%</Badge>
            </span>
            <div class="h-7 flex-1 overflow-hidden rounded-lg bg-zinc-100 dark:bg-zinc-800">
              <div
                class="flex h-full items-center justify-end rounded-lg pr-2 text-[11px] font-semibold text-white"
                :class="stage.probability === 100 ? 'bg-emerald-600' : stage.probability === 0 ? 'bg-zinc-400' : 'bg-sky-500'"
                :style="{ width: `${Math.max((stage.total / maxTotal) * 100, 12)}%` }"
              >{{ formatMoney2(stage.total.toFixed(2)) }}</div>
            </div>
            <span class="w-24 shrink-0 text-right text-[11px] text-muted-foreground">
              {{ stage.count }} · {{ t('reports.weighted') }} {{ formatMoney2(stage.weighted.toFixed(2)) }}
            </span>
          </div>
        </div>
      </CardContent>
    </Card>

    <!-- Экспорт 1C -->
    <Card v-if="canAccounting" class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="flex flex-wrap items-end gap-3 p-5">
        <div class="space-y-1">
          <Label class="text-xs text-muted-foreground">{{ t('reports.exportAccount') }}</Label>
          <div class="w-[240px]">
            <Select v-model="exportForm.accountId" :options="accounts.map((a) => ({ value: a.id, label: `${a.name} · ${a.currency}` }))" />
          </div>
        </div>
        <div class="space-y-1">
          <Label class="text-xs text-muted-foreground">{{ t('reports.exportFrom') }}</Label>
          <input
            v-model="exportForm.from" type="date"
            class="h-9 rounded-lg border border-input bg-background px-3 text-sm shadow-sm"
          >
        </div>
        <div class="space-y-1">
          <Label class="text-xs text-muted-foreground">{{ t('reports.exportTo') }}</Label>
          <input
            v-model="exportForm.to" type="date"
            class="h-9 rounded-lg border border-input bg-background px-3 text-sm shadow-sm"
          >
        </div>
        <Button variant="emerald" size="sm" class="gap-1.5" :disabled="exporting || !exportForm.accountId" @click="export1c">
          <Download class="h-3.5 w-3.5" /> {{ exporting ? t('reports.exporting') : t('reports.export1c') }}
        </Button>
        <p class="w-full text-xs text-muted-foreground">{{ t('reports.exportHint') }}</p>
      </CardContent>
    </Card>
  </div>
</template>

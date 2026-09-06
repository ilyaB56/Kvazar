<script setup lang="ts">
// Дашборд (этап F редизайна, стр. 3 макета): KPI из наших API, график ДДС
// по дням (собственный SVG, парные бары приход/расход), последние
// транзакции, просроченные задачи CRM, дебиторка по заказам продаж
// (confirmed, Σ base), лента событий events_log (админ; блок скрыт для
// не-админа). Блок «заполненность складов» скрыт до лимитов локаций (этап I),
// канбан задач — этап H. Клик по KPI ведёт в раздел; пустое — EmptyState.
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import {
  AlertOctagon, ArrowUpRight, ClipboardList, Landmark, TrendingUp, Wallet,
} from 'lucide-vue-next'
import { get } from '../api/client'
import { Badge, Button, Card, CardContent, EmptyState, Skeleton } from '../components/ui'
import { useAuthStore } from '../stores/auth'
import { formatMoney2 } from '../utils/money'

const { t, d } = useI18n()
const router = useRouter()
const auth = useAuthStore()

const canAccounting = computed(() => auth.moduleLevel('accounting') !== 'none')
const canCrm = computed(() => auth.moduleLevel('crm') !== 'none')

interface Cashflow {
  opening_balance: string
  closing_balance: string
  totals: Array<{ kind: string; category_id: string | null; total: string }>
}
interface Txn {
  id: string
  doc_number: string | null
  kind: string
  operated_at: string
  amount: string
  currency: string
  amount_base: string | null
  description: string
}
interface PipelineStage {
  stage: string
  probability: number
  count: number
  total: number
  weighted: number
  is_won?: boolean
  is_lost?: boolean
}
interface Activity {
  id: string
  title: string
  due_at: string | null
  done: boolean
}
interface SalesOrder {
  id: string
  number: string
  counterparty_id: string | null
  status: string
  amount_base: string | null
}
interface EventRow {
  id: number
  action: string
  entity_type: string
  created_at: string
}

const loading = ref(true)

// ---------- Финансы: месяц ----------
// локальный ISO-день: toISOString уползает на сутки при UTC+N
function localDay(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
const monthStart = new Date()
monthStart.setDate(1)
const from = localDay(monthStart)
const to = localDay(new Date())

const cashflow = ref<Cashflow | null>(null)
const txns = ref<Txn[]>([])

const incomeMonth = computed(() =>
  (cashflow.value?.totals ?? [])
    .filter((row) => row.kind === 'income')
    .reduce((sum, row) => sum + Number(row.total), 0))
const expenseMonth = computed(() =>
  (cashflow.value?.totals ?? [])
    .filter((row) => row.kind === 'expense')
    .reduce((sum, row) => sum + Math.abs(Number(row.total)), 0))
const netFlow = computed(() =>
  cashflow.value
    ? Number(cashflow.value.closing_balance) - Number(cashflow.value.opening_balance)
    : 0)

// ---------- CRM ----------
const pipeline = ref<PipelineStage[] | null>(null)
const overdueTasks = ref<Activity[]>([])

const openDeals = computed(() =>
  (pipeline.value ?? [])
    .filter((row) => row.probability > 0 && row.probability < 100)
    .reduce((sum, row) => sum + row.count, 0))
const weightedPipeline = computed(() =>
  (pipeline.value ?? [])
    .filter((row) => row.probability > 0 && row.probability < 100)
    .reduce((sum, row) => sum + row.weighted, 0))

// ---------- Дебиторка: заказы продаж к оплате (confirmed) ----------
const openOrders = ref<SalesOrder[]>([])
const receivableTotal = computed(() =>
  openOrders.value.reduce((sum, order) => sum + Number(order.amount_base ?? 0), 0))

// ---------- Лента событий (админ) ----------
const events = ref<EventRow[] | null>(null)

// ---------- График ДДС по дням (собственный SVG) ----------
const daily = computed(() => {
  const map = new Map<string, { in: number; out: number }>()
  for (const txn of txns.value) {
    if (txn.kind === 'transfer') continue
    const day = txn.operated_at
    const bucket = map.get(day) ?? { in: 0, out: 0 }
    // базовые суммы: график в одной валюте (₽)
    const value = Number(txn.amount_base ?? txn.amount)
    if (txn.kind === 'income') bucket.in += value
    else bucket.out += value
    map.set(day, bucket)
  }
  const days = [...map.entries()].sort((a, b) => a[0].localeCompare(b[0])).slice(-30)
  const max = Math.max(1, ...days.map(([, v]) => Math.max(v.in, v.out)))
  return { days, max }
})

interface Kpi {
  key: string
  label: string
  value: string
  hint: string
  icon: typeof Wallet
  to: string
}

const kpis = computed<Kpi[]>(() => {
  const list: Kpi[] = []
  if (canAccounting.value && cashflow.value) {
    list.push(
      {
        key: 'balance', label: t('dash.kpiBalance'), icon: Wallet, to: '/accounting?tab=transactions',
        value: formatMoney2(cashflow.value.closing_balance), hint: t('dash.kpiBalanceHint'),
      },
      {
        key: 'income', label: t('dash.kpiIncome'), icon: TrendingUp, to: '/accounting?tab=transactions',
        value: formatMoney2(incomeMonth.value.toFixed(2)), hint: t('dash.kpiFlowHint', { expense: formatMoney2(expenseMonth.value.toFixed(2)) }),
      },
      {
        key: 'net', label: t('dash.kpiNet'), icon: Landmark, to: '/accounting?tab=transactions',
        value: `${netFlow.value >= 0 ? '+' : '−'}${formatMoney2(Math.abs(netFlow.value).toFixed(2))}`,
        hint: t('dash.kpiNetHint'),
      },
    )
  }
  if (canCrm.value && pipeline.value) {
    list.push(
      {
        key: 'deals', label: t('dash.kpiDeals'), icon: ClipboardList, to: '/crm',
        value: String(openDeals.value), hint: t('dash.kpiDealsHint'),
      },
      {
        key: 'weighted', label: t('dash.kpiWeighted'), icon: TrendingUp, to: '/crm',
        value: formatMoney2(weightedPipeline.value.toFixed(2)), hint: t('dash.kpiWeightedHint'),
      },
    )
  }
  return list
})

const latestTxns = computed(() => [...txns.value].reverse().slice(0, 6))

async function load() {
  loading.value = true
  const jobs: Array<Promise<void>> = []
  if (canAccounting.value) {
    jobs.push(
      get<Cashflow>(`/accounting/report/cashflow?date_from=${from}&date_to=${to}`)
        .then((data) => { cashflow.value = data })
        .catch(() => { cashflow.value = null }),
      get<Txn[]>(`/accounting/transactions?date_from=${from}&date_to=${to}`)
        .then((data) => { txns.value = data })
        .catch(() => { txns.value = [] }),
      get<SalesOrder[]>('/accounting/sales-orders?status=confirmed')
        .then((data) => { openOrders.value = data })
        .catch(() => { openOrders.value = [] }),
    )
  }
  if (canCrm.value) {
    jobs.push(
      get<{ stages: PipelineStage[] }>('/crm/report/pipeline')
        .then((data) => { pipeline.value = data.stages })
        .catch(() => { pipeline.value = null }),
      get<Activity[]>('/crm/activities')
        .then((data) => {
          const today = localDay(new Date())
          overdueTasks.value = data
            .filter((a) => !a.done && a.due_at && a.due_at < today)
            .slice(0, 6)
        })
        .catch(() => { overdueTasks.value = [] }),
    )
  }
  if (auth.isAdmin) {
    jobs.push(
      get<EventRow[]>('/events/log?limit=8')
        .then((data) => { events.value = data })
        .catch(() => { events.value = null }),
    )
  }
  await Promise.all(jobs)
  loading.value = false
}
onMounted(load)
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div>
        <h2 class="text-lg font-bold tracking-tight">{{ t('dash.title') }}</h2>
        <p class="text-sm text-muted-foreground">{{ t('dash.subtitle') }}</p>
      </div>
    </div>

    <!-- KPI -->
    <div v-if="loading" class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Skeleton v-for="i in 4" :key="i" class="h-28 w-full" />
    </div>
    <div v-else-if="kpis.length" class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Card
        v-for="kpi in kpis" :key="kpi.key"
        class="group cursor-pointer border-zinc-200 shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md dark:border-zinc-800"
        @click="router.push(kpi.to)"
      >
        <CardContent class="p-5">
          <div class="flex items-center justify-between">
            <span class="text-sm text-muted-foreground">{{ kpi.label }}</span>
            <span class="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600 transition-transform group-hover:scale-110 dark:bg-emerald-950/60 dark:text-emerald-400">
              <component :is="kpi.icon" class="h-4 w-4" />
            </span>
          </div>
          <p class="mt-3 text-2xl font-bold tabular-nums tracking-tight">{{ kpi.value }}</p>
          <p class="mt-1 truncate text-xs text-muted-foreground">{{ kpi.hint }}</p>
        </CardContent>
      </Card>
    </div>
    <Card v-else class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-6">
        <EmptyState :title="t('ui.emptyTitle')" :description="t('dash.nothing')" />
      </CardContent>
    </Card>

    <!-- График ДДС + последние транзакции -->
    <div v-if="canAccounting" class="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Card class="border-zinc-200 shadow-sm dark:border-zinc-800 lg:col-span-2">
        <CardContent class="p-5">
          <div class="flex items-center justify-between">
            <p class="text-base font-semibold">{{ t('dash.cashflowTitle') }}</p>
            <div class="flex items-center gap-3 text-[11px] text-muted-foreground">
              <span class="flex items-center gap-1"><span class="h-2.5 w-2.5 rounded-sm bg-emerald-500" /> {{ t('dash.income') }}</span>
              <span class="flex items-center gap-1"><span class="h-2.5 w-2.5 rounded-sm bg-amber-500" /> {{ t('dash.expense') }}</span>
            </div>
          </div>
          <div v-if="daily.days.length" class="mt-4">
            <svg :viewBox="`0 0 560 ${180}`" class="w-full" role="img" preserveAspectRatio="none">
              <g v-for="(row, i) in daily.days" :key="row[0]">
                <rect
                  :x="16 + i * (528 / daily.days.length)" :width="Math.min(528 / daily.days.length / 2 - 2, 20)"
                  :y="8 + 156 * (1 - row[1].in / daily.max)" :height="Math.max(156 * (row[1].in / daily.max), 1)"
                  rx="3" fill="#10b981" opacity="0.85"
                ><title>{{ row[0] }} · {{ t('dash.income') }}: {{ formatMoney2(row[1].in.toFixed(2)) }}</title></rect>
                <rect
                  :x="16 + i * (528 / daily.days.length) + Math.min(528 / daily.days.length / 2, 22)"
                  :width="Math.min(528 / daily.days.length / 2 - 2, 20)"
                  :y="8 + 156 * (1 - row[1].out / daily.max)" :height="Math.max(156 * (row[1].out / daily.max), 1)"
                  rx="3" fill="#f59e0b" opacity="0.85"
                ><title>{{ row[0] }} · {{ t('dash.expense') }}: {{ formatMoney2(row[1].out.toFixed(2)) }}</title></rect>
              </g>
            </svg>
            <div class="mt-2 flex justify-between text-[11px] text-muted-foreground">
              <span>{{ daily.days[0]?.[0] }}</span>
              <span>{{ daily.days[daily.days.length - 1]?.[0] }}</span>
            </div>
          </div>
          <div v-else class="py-10">
            <EmptyState :title="t('ui.emptyTitle')" :description="t('dash.noCashflow')" />
          </div>
        </CardContent>
      </Card>

      <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="p-5">
          <p class="text-base font-semibold">{{ t('dash.latestTxns') }}</p>
          <ul v-if="latestTxns.length" class="mt-3 space-y-2.5">
            <li v-for="txn in latestTxns" :key="txn.id" class="flex items-center justify-between gap-2">
              <div class="min-w-0">
                <p class="truncate text-[13px] font-medium">{{ txn.description || txn.doc_number }}</p>
                <p class="text-[11px] text-muted-foreground">{{ txn.operated_at }} · {{ t(`finance.kind.${txn.kind}`) }}</p>
              </div>
              <span
                class="shrink-0 whitespace-nowrap text-[13px] font-semibold"
                :class="txn.kind === 'income' && 'text-emerald-600 dark:text-emerald-400'"
              >{{ formatMoney2(txn.amount, txn.currency) }}</span>
            </li>
          </ul>
          <div v-else class="py-8">
            <EmptyState :title="t('ui.emptyTitle')" :description="t('dash.noCashflow')" />
          </div>
        </CardContent>
      </Card>
    </div>

    <!-- Дебиторка + просроченные задачи -->
    <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Card
        v-if="canAccounting"
        class="border-zinc-200 shadow-sm dark:border-zinc-800"
        :class="openOrders.length ? 'border-red-200 dark:border-red-900/60' : 'border-emerald-200 dark:border-emerald-900/60'"
      >
        <CardContent class="p-5">
          <div class="flex items-center justify-between gap-2">
            <div class="flex items-center gap-2">
              <component :is="openOrders.length ? AlertOctagon : ClipboardList"
                :class="['h-5 w-5', openOrders.length ? 'text-red-600 dark:text-red-400' : 'text-emerald-600 dark:text-emerald-400']"
              />
              <p class="text-sm font-semibold">{{ t('dash.receivable') }}</p>
              <Badge v-if="openOrders.length" class="bg-red-500 text-[10px] text-white">
                {{ openOrders.length }}
              </Badge>
            </div>
            <Button variant="outline" size="sm" class="gap-1.5" @click="router.push('/accounting')">
              {{ t('dash.openFinance') }} <ArrowUpRight class="h-3.5 w-3.5" />
            </Button>
          </div>
          <p v-if="openOrders.length === 0" class="mt-3 text-sm text-emerald-700 dark:text-emerald-300">
            {{ t('dash.receivableOk') }}
          </p>
          <template v-else>
            <p class="mt-3 text-[11px] font-semibold uppercase tracking-wide text-red-700 dark:text-red-400">
              {{ t('dash.receivableSum') }} · {{ formatMoney2(receivableTotal.toFixed(2)) }}
            </p>
            <ul class="mt-2 space-y-1.5">
              <li v-for="order in openOrders.slice(0, 5)" :key="order.id"
                  class="flex items-center justify-between rounded-lg border border-red-200 bg-white px-3 py-2 dark:border-red-900/60 dark:bg-zinc-900/70"
              >
                <span class="text-[13px] font-medium">{{ order.number }}</span>
                <span class="text-[13px] font-semibold text-red-700 dark:text-red-400">
                  {{ formatMoney2(order.amount_base ?? '0') }}
                </span>
              </li>
            </ul>
            <p class="mt-2 text-[11px] text-muted-foreground">{{ t('dash.receivableNote') }}</p>
          </template>
        </CardContent>
      </Card>

      <Card v-if="canCrm" class="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="p-5">
          <div class="flex items-center justify-between gap-2">
            <p class="flex items-center gap-2 text-sm font-semibold">
              <AlertOctagon :class="['h-5 w-5', overdueTasks.length ? 'text-amber-600 dark:text-amber-400' : 'text-emerald-600 dark:text-emerald-400']" />
              {{ t('dash.overdueTasks') }}
              <Badge v-if="overdueTasks.length" class="bg-amber-500 text-[10px] text-white">{{ overdueTasks.length }}</Badge>
            </p>
            <Button variant="outline" size="sm" class="gap-1.5" @click="router.push('/crm')">
              {{ t('dash.openCrm') }} <ArrowUpRight class="h-3.5 w-3.5" />
            </Button>
          </div>
          <ul v-if="overdueTasks.length" class="mt-3 space-y-1.5">
            <li v-for="a in overdueTasks" :key="a.id"
                class="flex items-center justify-between rounded-lg border border-amber-200 bg-white px-3 py-2 dark:border-amber-900/60 dark:bg-zinc-900/70"
            >
              <span class="min-w-0 truncate text-[13px] font-medium">{{ a.title }}</span>
              <span class="shrink-0 text-[11px] font-semibold text-amber-700 dark:text-amber-400">{{ a.due_at }}</span>
            </li>
          </ul>
          <p v-else class="mt-3 text-sm text-emerald-700 dark:text-emerald-300">{{ t('dash.tasksOk') }}</p>
        </CardContent>
      </Card>
    </div>

    <!-- Лента событий (админ; скрыта для остальных) -->
    <Card v-if="auth.isAdmin && events" class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-5">
        <p class="text-base font-semibold">{{ t('dash.eventFeed') }}</p>
        <ul class="mt-3 space-y-2.5">
          <li v-for="event in events" :key="event.id" class="flex items-start gap-2.5">
            <span class="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-sky-100 text-sky-700 dark:bg-sky-950/60 dark:text-sky-300">
              <Landmark class="h-3.5 w-3.5" />
            </span>
            <div class="min-w-0 flex-1">
              <p class="text-[13px] leading-snug">
                <span class="font-medium">{{ event.action }}</span>
                <span class="text-muted-foreground"> · {{ event.entity_type }}</span>
              </p>
              <p class="text-[11px] text-muted-foreground">{{ d(event.created_at, 'short') }}</p>
            </div>
          </li>
        </ul>
      </CardContent>
    </Card>
  </div>
</template>

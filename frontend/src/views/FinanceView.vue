<script setup lang="ts">
// Финансы (этап D редизайна, стр. 7 макета / §7-251): вкладки Транзакции /
// Счета / Категории / Контрагенты / Курсы / Периоды. Суммы — только строки
// с сервера, отображение через utils/money.ts (ADR-003: 4 знака из API →
// 2 на экран). Кнопки создания/сторнирования видны только при accounting: rw
// (ro-пользователь читает); защита при этом серверная (require_module).
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowDownLeft, ArrowUpRight, ArrowLeftRight, Ban, Check, Plus, Send, Trash2,
} from 'lucide-vue-next'
import { del, get, post } from '../api/client'
import type { Account, Category, Counterparty, Period, Rate, Transaction } from '../api/types'
import {
  Badge, Button, Card, CardContent, Dialog, EmptyState, Input, Label,
  PaginatedList, Select, Skeleton, StatusBadge, Tabs, useToast,
} from '../components/ui'
import type { PageOf, StatusTone } from '../components/ui'
import { useAuthStore } from '../stores/auth'
import { formatMoney2, formatRate, isPositiveDecimalString } from '../utils/money'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const toast = useToast()

const canWrite = computed(() => auth.moduleLevel('accounting') === 'rw')

// локальные строки поиска справочников (гейт 1.1a); q уходит на сервер
const refSearch = reactive({ accounts: '', categories: '', counterparties: '' })
const TABS = ['transactions', 'accounts', 'categories', 'counterparties', 'rates', 'periods'] as const
type TabKey = typeof TABS[number]
const tab = ref<TabKey>((route.query.tab as TabKey) in TABS || TABS.includes(route.query.tab as TabKey) ? (route.query.tab as TabKey) : 'transactions')
watch(tab, (value) => { void router.replace({ query: { ...route.query, tab: value } }) })

// ---------- Справочники + транзакции ----------

const loading = ref(true)
const accounts = ref<Account[]>([])
const categories = ref<Category[]>([])
const counterparties = ref<Counterparty[]>([])

const filters = reactive({
  dateFrom: '',
  dateTo: '',
  accountId: '',
  categoryId: '',
})

function accountName(id: string | null | undefined): string {
  return id ? (accounts.value.find((a) => a.id === id)?.name ?? '…') : ''
}
function categoryName(id: string | null | undefined): string {
  return id ? (categories.value.find((c) => c.id === id)?.name ?? '…') : ''
}
function counterpartyName(id: string | null | undefined): string {
  return id ? (counterparties.value.find((k) => k.id === id)?.name ?? '…') : ''
}

async function loadReference() {
  const [a, c, k] = await Promise.all([
    get<Account[]>('/accounting/accounts'),
    get<Category[]>('/accounting/categories'),
    get<Counterparty[]>('/accounting/counterparties'),
  ])
  accounts.value = a
  categories.value = c
  counterparties.value = k
}

// транзакции пагинированы (по 50 + infinite scroll); смена фильтров
// меняет resetKey — список перезагружается с нуля, offset сбрасывается
const txnList = ref<{ reload: () => Promise<void> } | null>(null)
const txnKey = ref('init')

function fetchTxnPage(offset: number, limit: number) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (filters.dateFrom) params.set('date_from', filters.dateFrom)
  if (filters.dateTo) params.set('date_to', filters.dateTo)
  if (filters.accountId) params.set('account_id', filters.accountId)
  if (filters.categoryId) params.set('category_id', filters.categoryId)
  return get<PageOf<Transaction>>(`/accounting/transactions?${params}`)
}

const rateList = ref<{ reload: () => Promise<void> } | null>(null)
const periodList = ref<{ reload: () => Promise<void> } | null>(null)
const fetchRatePage = (offset: number, limit: number) =>
  get<PageOf<Rate>>(`/accounting/rates?limit=${limit}&offset=${offset}`)
const fetchPeriodPage = (offset: number, limit: number) =>
  get<PageOf<Period>>(`/accounting/periods?limit=${limit}&offset=${offset}`)

async function loadAll() {
  loading.value = true
  try {
    await loadReference()
  } catch {
    toast.error(t('errors.unknown'))
  } finally {
    loading.value = false
  }
}
onMounted(loadAll)

let filterTimer: number | undefined
watch(filters, () => {
  window.clearTimeout(filterTimer)
  filterTimer = window.setTimeout(() => {
    txnKey.value = [filters.dateFrom, filters.dateTo, filters.accountId, filters.categoryId].join('|')
  }, 300)
})

// q справочников уходит на сервер внутри PaginatedList; тут — только
// дебаунс resetKey (поиск сбрасывает offset на 0). Карты имён для
// таблицы транзакций (accountName/…) грузятся один раз на маунте.
const refKey = reactive({ accounts: '', categories: '', counterparties: '' })
let refTimer: number | undefined
watch(() => [refSearch.accounts, refSearch.categories, refSearch.counterparties],
  ([a, c, k]) => {
    window.clearTimeout(refTimer)
    refTimer = window.setTimeout(() => {
      refKey.accounts = a
      refKey.categories = c
      refKey.counterparties = k
    }, 300)
  })

const accountList = ref<{ reload: () => Promise<void> } | null>(null)
const categoryList = ref<{ reload: () => Promise<void> } | null>(null)
const counterpartyList = ref<{ reload: () => Promise<void> } | null>(null)

function fetchAccountsPage(offset: number, limit: number) {
  const qs = refKey.accounts.trim() ? `&q=${encodeURIComponent(refKey.accounts.trim())}` : ''
  return get<PageOf<Account>>(`/accounting/accounts?limit=${limit}&offset=${offset}${qs}`)
}
function fetchCategoriesPage(offset: number, limit: number) {
  const qs = refKey.categories.trim() ? `&q=${encodeURIComponent(refKey.categories.trim())}` : ''
  return get<PageOf<Category>>(`/accounting/categories?limit=${limit}&offset=${offset}${qs}`)
}
function fetchCounterpartiesPage(offset: number, limit: number) {
  const qs = refKey.counterparties.trim() ? `&q=${encodeURIComponent(refKey.counterparties.trim())}` : ''
  return get<PageOf<Counterparty>>(`/accounting/counterparties?limit=${limit}&offset=${offset}${qs}`)
}

// ---------- Отображение ----------

const kindMeta: Record<string, { icon: typeof ArrowUpRight; cls: string }> = {
  income: { icon: ArrowDownLeft, cls: 'bg-emerald-50 text-emerald-600 dark:bg-emerald-950/60 dark:text-emerald-400' },
  expense: { icon: ArrowUpRight, cls: 'bg-amber-50 text-amber-600 dark:bg-amber-950/60 dark:text-amber-400' },
  transfer: { icon: ArrowLeftRight, cls: 'bg-sky-50 text-sky-600 dark:bg-sky-950/60 dark:text-sky-400' },
}

function statusMeta(status: string): { tone: StatusTone; label: string } {
  const map: Record<string, { tone: StatusTone; key: string }> = {
    draft: { tone: 'amber', key: 'statusDraft' },
    posted: { tone: 'emerald', key: 'statusPosted' },
    storno: { tone: 'red', key: 'statusStorno' },
    deleted: { tone: 'zinc', key: 'statusDeleted' },
  }
  const meta = map[status] ?? { tone: 'zinc' as StatusTone, key: 'statusPosted' }
  return { tone: meta.tone, label: t(`finance.${meta.key}`) }
}

function txnDirection(txn: Transaction): 'plus' | 'minus' | 'none' {
  if (txn.kind === 'income') return 'plus'
  if (txn.kind === 'expense') return 'minus'
  return 'none'
}

// ---------- Диалог создания транзакции ----------

const txnOpen = ref(false)
const txnSaving = ref(false)
const txnForm = reactive({
  kind: 'expense',
  operatedAt: new Date().toISOString().slice(0, 10),
  amount: '',
  currency: 'RUB',
  accountId: '',
  accountToId: '',
  categoryId: '',
  counterpartyId: '',
  description: '',
  postImmediately: false,
})
// валюта операции = валюта счёта (правило API: несовпадение даёт 422)
const accountCurrency = computed(
  () => accounts.value.find((a) => a.id === txnForm.accountId)?.currency ?? 'RUB',
)
const isForeign = computed(() => accountCurrency.value !== 'RUB')
const currentRate = ref<string | null>(null)
const freezeRate = ref(false)
const freezeRateValue = ref('')

async function refreshCurrentRate() {
  currentRate.value = null
  if (!isForeign.value || !txnForm.operatedAt) return
  try {
    const rows = await get<Rate[]>(`/accounting/rates?currency=${accountCurrency.value}&date_to=${txnForm.operatedAt}`)
    currentRate.value = rows[0]?.rate ?? null
  } catch {
    currentRate.value = null
  }
}

watch(() => [accountCurrency.value, txnForm.operatedAt], () => { void refreshCurrentRate() })

function openTxnDialog() {
  txnForm.kind = 'expense'
  txnForm.operatedAt = new Date().toISOString().slice(0, 10)
  txnForm.amount = ''
  txnForm.currency = 'RUB'
  txnForm.accountId = accounts.value[0]?.id ?? ''
  txnForm.accountToId = ''
  txnForm.categoryId = ''
  txnForm.counterpartyId = ''
  txnForm.description = ''
  txnForm.postImmediately = false
  freezeRate.value = false
  freezeRateValue.value = ''
  currentRate.value = null
  txnOpen.value = true
}

async function saveTxn() {
  if (txnSaving.value) return
  if (!txnForm.accountId || !isPositiveDecimalString(txnForm.amount)) {
    toast.error(t('finance.validation'))
    return
  }
  if (txnForm.kind === 'transfer' && !txnForm.accountToId) {
    toast.error(t('finance.validationTransfer'))
    return
  }
  txnSaving.value = true
  try {
    // заморозка курса: ручной курс на дату BEFORE транзакции — проведение
    // возьмёт именно его (upsert /accounting/rates)
    if (isForeign.value && freezeRate.value && isPositiveDecimalString(freezeRateValue.value)) {
      await post('/accounting/rates', {
        date: txnForm.operatedAt,
        currency: accountCurrency.value,
        rate: freezeRateValue.value.trim().replace(',', '.'),
      })
    }
    const created = await post<Transaction>('/accounting/transactions', {
      kind: txnForm.kind,
      operated_at: txnForm.operatedAt,
      amount: txnForm.amount.trim().replace(',', '.'),
      currency: accountCurrency.value,
      account_id: txnForm.accountId,
      account_to_id: txnForm.kind === 'transfer' ? txnForm.accountToId : null,
      category_id: txnForm.categoryId || null,
      counterparty_id: txnForm.counterpartyId || null,
      description: txnForm.description,
      post_immediately: txnForm.postImmediately,
    })
    toast.success(
      txnForm.postImmediately ? t('finance.createdPosted') : t('finance.createdDraft'),
      created.doc_number ?? undefined,
    )
    txnOpen.value = false
    void txnList.value?.reload()
    void rateList.value?.reload()
  } catch (error) {
    toast.apiError(error)
  } finally {
    txnSaving.value = false
  }
}

// ---------- Действия по транзакции ----------

async function postTxn(txn: Transaction) {
  try {
    await post<Transaction>(`/accounting/transactions/${txn.id}/post`)
    toast.success(t('finance.posted'), txn.doc_number ?? undefined)
    void txnList.value?.reload()
  } catch {
    toast.error(t('errors.unknown'))
  }
}

async function deleteDraft(txn: Transaction) {
  try {
    await del(`/accounting/transactions/${txn.id}`)
    toast.success(t('finance.deleted'))
    void txnList.value?.reload()
  } catch {
    toast.error(t('errors.unknown'))
  }
}

const stornoTarget = ref<Transaction | null>(null)
const stornoReason = ref('')
const stornoWorking = ref(false)

async function doStorno() {
  if (!stornoTarget.value || stornoWorking.value) return
  stornoWorking.value = true
  try {
    await post<Transaction>(`/accounting/transactions/${stornoTarget.value.id}/storno`, {
      reason: stornoReason.value.trim(),
    })
    toast.success(t('finance.stornoed'))
    stornoTarget.value = null
    stornoReason.value = ''
    void txnList.value?.reload()
  } catch {
    toast.error(t('errors.unknown'))
  } finally {
    stornoWorking.value = false
  }
}

// ---------- Справочники: создание ----------

const refDialog = ref<null | 'account' | 'category' | 'counterparty'>(null)
const refSaving = ref(false)
const accountForm = reactive({ name: '', currency: 'RUB' })
const categoryForm = reactive({ name: '', kind: 'expense' })
const counterpartyForm = reactive({ name: '', inn: '' })

async function saveReference() {
  if (refSaving.value) return
  refSaving.value = true
  try {
    if (refDialog.value === 'account' && accountForm.name.trim()) {
      await post('/accounting/accounts', { name: accountForm.name.trim(), currency: accountForm.currency })
      toast.success(t('finance.accountCreated'))
    } else if (refDialog.value === 'category' && categoryForm.name.trim()) {
      await post('/accounting/categories', { name: categoryForm.name.trim(), kind: categoryForm.kind })
      toast.success(t('finance.categoryCreated'))
    } else if (refDialog.value === 'counterparty' && counterpartyForm.name.trim()) {
      await post('/accounting/counterparties', { name: counterpartyForm.name.trim(), inn: counterpartyForm.inn.trim() })
      toast.success(t('finance.counterpartyCreated'))
    }
    refDialog.value = null
    accountForm.name = ''
    categoryForm.name = ''
    counterpartyForm.name = ''
    await loadReference()
    void accountList.value?.reload()
    void categoryList.value?.reload()
    void counterpartyList.value?.reload()
  } catch {
    toast.error(t('errors.unknown'))
  } finally {
    refSaving.value = false
  }
}

// ---------- Курсы: ручная установка ----------

const rateForm = reactive({ date: new Date().toISOString().slice(0, 10), currency: 'USD', rate: '' })
const rateSaving = ref(false)

async function saveRate() {
  if (rateSaving.value || !isPositiveDecimalString(rateForm.rate)) return
  rateSaving.value = true
  try {
    await post('/accounting/rates', {
      date: rateForm.date, currency: rateForm.currency, rate: rateForm.rate.trim().replace(',', '.'),
    })
    toast.success(t('finance.rateSaved'))
    rateForm.rate = ''
    void rateList.value?.reload()
  } catch {
    toast.error(t('errors.unknown'))
  } finally {
    rateSaving.value = false
  }
}

// ---------- Периоды ----------

async function switchPeriod(period: Period, action: 'close' | 'reopen') {
  try {
    await post<Period>(`/accounting/periods/${period.year}/${period.month}/${action}`)
    toast.success(t(action === 'close' ? 'finance.periodClosed' : 'finance.periodReopened'))
    void periodList.value?.reload()
  } catch {
    toast.error(t('errors.unknown'))
  }
}

const currencyOptions = [
  { value: 'RUB', label: 'RUB ₽' }, { value: 'USD', label: 'USD $' },
  { value: 'EUR', label: 'EUR €' }, { value: 'CNY', label: 'CNY ¥' },
]
const tabsList = computed(() => [
  { key: 'transactions', label: t('finance.tabs.transactions') },
  { key: 'accounts', label: t('finance.tabs.accounts') },
  { key: 'categories', label: t('finance.tabs.categories') },
  { key: 'counterparties', label: t('finance.tabs.counterparties') },
  { key: 'rates', label: t('finance.tabs.rates') },
  { key: 'periods', label: t('finance.tabs.periods') },
])
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div>
        <h2 class="text-lg font-bold tracking-tight">{{ t('finance.title') }}</h2>
        <p class="text-sm text-muted-foreground">{{ t('finance.subtitle') }}</p>
      </div>
      <Button
        v-if="canWrite" variant="emerald" size="sm" class="gap-1.5"
        @click="openTxnDialog"
      >
        <Plus class="h-4 w-4" /> {{ t('finance.newTransaction') }}
      </Button>
    </div>

    <Tabs :tabs="tabsList" :model-value="tab" @update:model-value="tab = $event as TabKey" />

    <!-- Транзакции -->
    <div v-if="tab === 'transactions'" class="space-y-4">
      <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="flex flex-wrap items-end gap-3 p-4">
          <div class="space-y-1">
            <Label class="text-xs text-muted-foreground">{{ t('finance.filterPeriodFrom') }}</Label>
            <Input v-model="filters.dateFrom" type="date" class="h-9 w-[150px]" />
          </div>
          <div class="space-y-1">
            <Label class="text-xs text-muted-foreground">{{ t('finance.filterPeriodTo') }}</Label>
            <Input v-model="filters.dateTo" type="date" class="h-9 w-[150px]" />
          </div>
          <div class="w-[200px] space-y-1">
            <Label class="text-xs text-muted-foreground">{{ t('finance.filterAccount') }}</Label>
            <Select v-model="filters.accountId" :options="[
              { value: '', label: t('finance.allAccounts') },
              ...accounts.map((a) => ({ value: a.id, label: a.name })),
            ]" />
          </div>
          <div class="w-[220px] space-y-1">
            <Label class="text-xs text-muted-foreground">{{ t('finance.filterCategory') }}</Label>
            <Select v-model="filters.categoryId" :options="[
              { value: '', label: t('finance.allCategories') },
              ...categories.map((c) => ({ value: c.id, label: c.name })),
            ]" />
          </div>
          <Button
            variant="ghost" size="sm"
            @click="filters.dateFrom = ''; filters.dateTo = ''; filters.accountId = ''; filters.categoryId = ''"
          >{{ t('finance.resetFilters') }}</Button>
        </CardContent>
      </Card>

      <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="p-0">
          <PaginatedList ref="txnList" :fetch-page="fetchTxnPage" :reset-key="txnKey" v-slot="{ items: txns, loading }">
          <div v-if="loading" class="space-y-2 p-4">
            <Skeleton class="h-10 w-full" />
            <Skeleton class="h-10 w-full" />
            <Skeleton class="h-10 w-full" />
          </div>
          <div v-else-if="txns.length === 0" class="p-6">
            <EmptyState :title="t('ui.emptyTitle')" :description="t('ui.emptyDescription')" />
          </div>
          <div v-else class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                  <th class="px-3 py-2 font-medium">{{ t('finance.colDoc') }}</th>
                  <th class="px-3 py-2 font-medium">{{ t('finance.colDate') }}</th>
                  <th class="px-3 py-2 font-medium">{{ t('finance.colKind') }}</th>
                  <th class="hidden px-3 py-2 font-medium lg:table-cell">{{ t('finance.colAccounts') }}</th>
                  <th class="hidden px-3 py-2 font-medium md:table-cell">{{ t('finance.colCategory') }}</th>
                  <th class="hidden px-3 py-2 font-medium xl:table-cell">{{ t('finance.colCounterparty') }}</th>
                  <th class="px-3 py-2 text-right font-medium">{{ t('finance.colAmount') }}</th>
                  <th class="px-3 py-2 font-medium">{{ t('finance.colStatus') }}</th>
                  <th v-if="canWrite" class="px-3 py-2 font-medium" />
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="txn in txns" :key="txn.id"
                  class="border-t border-zinc-100 hover:bg-zinc-50/60 dark:border-zinc-800/70 dark:hover:bg-zinc-800/40"
                  :class="txn.status === 'storno' && 'opacity-60'"
                >
                  <td class="whitespace-nowrap px-3 py-2 font-medium text-emerald-700 dark:text-emerald-400">
                    {{ txn.doc_number ?? '—' }}
                  </td>
                  <td class="whitespace-nowrap px-3 py-2 text-muted-foreground">{{ txn.operated_at }}</td>
                  <td class="px-3 py-2">
                    <span
                      :class="['inline-flex h-7 w-7 items-center justify-center rounded-lg', kindMeta[txn.kind]?.cls]"
                      :title="t(`finance.kind.${txn.kind}`)"
                    >
                      <component :is="kindMeta[txn.kind]?.icon" class="h-4 w-4" />
                    </span>
                  </td>
                  <td class="hidden px-3 py-2 lg:table-cell">
                    <template v-if="txn.kind === 'transfer'">
                      {{ accountName(txn.account_id) }} → {{ accountName(txn.account_to_id) }}
                    </template>
                    <template v-else>{{ accountName(txn.account_id) }}</template>
                  </td>
                  <td class="hidden px-3 py-2 text-muted-foreground md:table-cell">
                    {{ categoryName(txn.category_id) || '—' }}
                  </td>
                  <td class="hidden px-3 py-2 text-muted-foreground xl:table-cell">
                    {{ counterpartyName(txn.counterparty_id) || '—' }}
                  </td>
                  <td class="whitespace-nowrap px-3 py-2 text-right font-semibold" :class="[
                    txnDirection(txn) === 'plus' && 'text-emerald-600 dark:text-emerald-400',
                    txnDirection(txn) === 'minus' && 'text-foreground',
                  ]">
                    {{ formatMoney2(txn.amount, txn.currency) }}
                    <span v-if="txn.rate" class="block text-[10px] font-normal text-muted-foreground">
                      ×{{ formatRate(txn.rate) }}
                    </span>
                  </td>
                  <td class="px-3 py-2"><StatusBadge v-bind="statusMeta(txn.status)" /></td>
                  <td v-if="canWrite" class="px-3 py-2">
                    <div v-if="txn.status === 'draft'" class="flex items-center gap-1">
                      <Button variant="ghost" size="icon" class="h-7 w-7" :title="t('finance.post')" @click="postTxn(txn)">
                        <Check class="h-3.5 w-3.5 text-emerald-600" />
                      </Button>
                      <Button variant="ghost" size="icon" class="h-7 w-7" :title="t('finance.delete')" @click="deleteDraft(txn)">
                        <Trash2 class="h-3.5 w-3.5 text-red-500" />
                      </Button>
                    </div>
                    <Button
                      v-else-if="txn.status === 'posted'" variant="ghost" size="icon" class="h-7 w-7"
                      :title="t('finance.storno')" @click="stornoTarget = txn"
                    >
                      <Ban class="h-3.5 w-3.5 text-amber-600" />
                    </Button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          </PaginatedList>
        </CardContent>
      </Card>
    </div>

    <!-- Счета -->
    <Card v-else-if="tab === 'accounts'" class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-0">
        <div class="flex items-center justify-between gap-3 flex-wrap px-4 py-3">
          <p class="text-sm font-semibold min-w-0 truncate">{{ t('finance.tabs.accounts') }}</p>
          <Input v-model="refSearch.accounts" :placeholder="t('ui.searchPlaceholder')" class="h-8 w-[220px] shrink-0" />
          <Button v-if="canWrite" variant="outline" size="sm" class="gap-1.5" @click="refDialog = 'account'">
            <Plus class="h-3.5 w-3.5" /> {{ t('finance.newAccount') }}
          </Button>
        </div>
        <PaginatedList ref="accountList" :fetch-page="fetchAccountsPage" :reset-key="`a:${refKey.accounts}`" v-slot="{ items: accountRows, loading: refLoading }">
        <div v-if="refLoading" class="space-y-2 p-4">
          <Skeleton class="h-10 w-full" /><Skeleton class="h-10 w-full" />
        </div>
        <div v-else-if="!accountRows.length" class="p-6">
          <EmptyState :title="t('ui.emptyTitle')" :description="t('ui.emptyDescription')" />
        </div>
        <div v-else class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                <th class="px-3 py-2 font-medium">{{ t('finance.colName') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('finance.colCurrency') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('finance.colNumber') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('finance.colActive') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="a in accountRows" :key="a.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
                <td class="px-3 py-2 font-medium">{{ a.name }}</td>
                <td class="px-3 py-2">{{ a.currency }}</td>
                <td class="px-3 py-2 text-muted-foreground">{{ a.account_number ?? '—' }}</td>
                <td class="px-3 py-2">
                  <Badge v-if="a.is_active" class="bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300">ok</Badge>
                  <Badge v-else variant="secondary">—</Badge>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        </PaginatedList>
      </CardContent>
    </Card>

    <!-- Категории -->
    <Card v-else-if="tab === 'categories'" class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-0">
        <div class="flex items-center justify-between gap-3 flex-wrap px-4 py-3">
          <p class="text-sm font-semibold min-w-0 truncate">{{ t('finance.tabs.categories') }}</p>
          <Input v-model="refSearch.categories" :placeholder="t('ui.searchPlaceholder')" class="h-8 w-[220px] shrink-0" />
          <Button v-if="canWrite" variant="outline" size="sm" class="gap-1.5" @click="refDialog = 'category'">
            <Plus class="h-3.5 w-3.5" /> {{ t('finance.newCategory') }}
          </Button>
        </div>
        <PaginatedList ref="categoryList" :fetch-page="fetchCategoriesPage" :reset-key="`c:${refKey.categories}`" v-slot="{ items: categoryRows, loading: refLoading }">
        <div v-if="refLoading" class="space-y-2 p-4">
          <Skeleton class="h-10 w-full" /><Skeleton class="h-10 w-full" />
        </div>
        <div v-else-if="!categoryRows.length" class="p-6">
          <EmptyState :title="t('ui.emptyTitle')" :description="t('ui.emptyDescription')" />
        </div>
        <div v-else class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                <th class="px-3 py-2 font-medium">{{ t('finance.colName') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('finance.colKind') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('finance.colActive') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="c in categoryRows" :key="c.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
                <td class="px-3 py-2 font-medium">{{ c.name }}</td>
                <td class="px-3 py-2">{{ t(`finance.kind.${c.kind}`) }}</td>
                <td class="px-3 py-2 text-muted-foreground">{{ c.is_active ? '✓' : '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        </PaginatedList>
      </CardContent>
    </Card>

    <!-- Контрагенты -->
    <Card v-else-if="tab === 'counterparties'" class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-0">
        <div class="flex items-center justify-between gap-3 flex-wrap px-4 py-3">
          <p class="text-sm font-semibold min-w-0 truncate">{{ t('finance.tabs.counterparties') }}</p>
          <Input v-model="refSearch.counterparties" :placeholder="t('ui.searchPlaceholder')" class="h-8 w-[220px] shrink-0" />
          <Button v-if="canWrite" variant="outline" size="sm" class="gap-1.5" @click="refDialog = 'counterparty'">
            <Plus class="h-3.5 w-3.5" /> {{ t('finance.newCounterparty') }}
          </Button>
        </div>
        <PaginatedList ref="counterpartyList" :fetch-page="fetchCounterpartiesPage" :reset-key="`k:${refKey.counterparties}`" v-slot="{ items: counterpartyRows, loading: refLoading }">
        <div v-if="refLoading" class="space-y-2 p-4">
          <Skeleton class="h-10 w-full" /><Skeleton class="h-10 w-full" />
        </div>
        <div v-else-if="!counterpartyRows.length" class="p-6">
          <EmptyState :title="t('ui.emptyTitle')" :description="t('ui.emptyDescription')" />
        </div>
        <div v-else class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                <th class="px-3 py-2 font-medium">{{ t('finance.colCode') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('finance.colName') }}</th>
                <th class="px-3 py-2 font-medium">ИНН</th>
                <th class="px-3 py-2 font-medium">КПП</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="k in counterpartyRows" :key="k.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
                <td class="px-3 py-2 font-medium text-emerald-700 dark:text-emerald-400">{{ k.internal_code }}</td>
                <td class="px-3 py-2">{{ k.name }}</td>
                <td class="px-3 py-2 text-muted-foreground">{{ k.inn || '—' }}</td>
                <td class="px-3 py-2 text-muted-foreground">{{ k.kpp || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        </PaginatedList>
      </CardContent>
    </Card>

    <!-- Курсы -->
    <div v-else-if="tab === 'rates'" class="space-y-4">
      <Card v-if="canWrite" class="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="flex flex-wrap items-end gap-3 p-4">
          <div class="space-y-1">
            <Label class="text-xs text-muted-foreground">{{ t('finance.rateDate') }}</Label>
            <Input v-model="rateForm.date" type="date" class="h-9 w-[150px]" />
          </div>
          <div class="w-[110px] space-y-1">
            <Label class="text-xs text-muted-foreground">{{ t('finance.colCurrency') }}</Label>
            <Select v-model="rateForm.currency" :options="currencyOptions" />
          </div>
          <div class="space-y-1">
            <Label class="text-xs text-muted-foreground">{{ t('finance.rateValue') }}</Label>
            <Input v-model="rateForm.rate" placeholder="97,5000" class="h-9 w-[130px]" />
          </div>
          <Button variant="emerald" size="sm" :disabled="rateSaving || !isPositiveDecimalString(rateForm.rate)" @click="saveRate">
            <Send class="h-3.5 w-3.5" /> {{ t('finance.saveRate') }}
          </Button>
          <p class="text-xs text-muted-foreground">{{ t('finance.rateHint') }}</p>
        </CardContent>
      </Card>
      <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="p-0">
          <PaginatedList ref="rateList" :fetch-page="fetchRatePage" v-slot="{ items: rateRows, loading: refLoading }">
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                  <th class="px-3 py-2 font-medium">{{ t('finance.colDate') }}</th>
                  <th class="px-3 py-2 font-medium">{{ t('finance.colCurrency') }}</th>
                  <th class="px-3 py-2 text-right font-medium">{{ t('finance.rateValue') }}</th>
                  <th class="px-3 py-2 font-medium">{{ t('finance.colSource') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="r in rateRows" :key="`${r.date}-${r.currency}`" class="border-t border-zinc-100 dark:border-zinc-800/70">
                  <td class="whitespace-nowrap px-3 py-2">{{ r.date }}</td>
                  <td class="px-3 py-2">{{ r.currency }}</td>
                  <td class="px-3 py-2 text-right font-semibold">{{ formatRate(r.rate) }}</td>
                  <td class="px-3 py-2">
                    <Badge variant="outline" :class="r.source === 'manual'
                      ? 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300'
                      : 'border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-300'">
                      {{ r.source }}
                    </Badge>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          </PaginatedList>
        </CardContent>
      </Card>
    </div>

    <!-- Периоды -->
    <Card v-else-if="tab === 'periods'" class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-0">
        <PaginatedList ref="periodList" :fetch-page="fetchPeriodPage" v-slot="{ items: periodRows }">
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                <th class="px-3 py-2 font-medium">{{ t('finance.colPeriod') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('finance.colStatus') }}</th>
                <th v-if="auth.isAdmin" class="px-3 py-2 font-medium" />
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in periodRows" :key="`${p.year}-${p.month}`" class="border-t border-zinc-100 dark:border-zinc-800/70">
                <td class="px-3 py-2 font-medium">{{ p.year }} · {{ String(p.month).padStart(2, '0') }}</td>
                <td class="px-3 py-2">
                  <Badge v-if="p.status === 'closed'" class="bg-red-100 text-red-800 dark:bg-red-950/60 dark:text-red-300">
                    {{ t('finance.periodClosedLabel') }}
                  </Badge>
                  <Badge v-else variant="secondary">{{ t('finance.periodOpenLabel') }}</Badge>
                </td>
                <td v-if="auth.isAdmin" class="px-3 py-2">
                  <Button
                    v-if="p.status === 'open'" variant="ghost" size="sm" class="text-red-600"
                    @click="switchPeriod(p, 'close')"
                  >{{ t('finance.periodClose') }}</Button>
                  <Button v-else variant="ghost" size="sm" @click="switchPeriod(p, 'reopen')">
                    {{ t('finance.periodReopen') }}
                  </Button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        </PaginatedList>
      </CardContent>
    </Card>

    <!-- Диалог создания транзакции -->
    <Dialog v-model:open="txnOpen" :title="t('finance.newTransaction')" width="560px">
      <form class="space-y-4" @submit.prevent="saveTxn">
        <div class="grid grid-cols-2 gap-3">
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('finance.colKind') }}</Label>
            <Select v-model="txnForm.kind" :options="[
              { value: 'expense', label: t('finance.kind.expense') },
              { value: 'income', label: t('finance.kind.income') },
              { value: 'transfer', label: t('finance.kind.transfer') },
            ]" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('finance.colDate') }}</Label>
            <Input v-model="txnForm.operatedAt" type="date" />
          </div>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('finance.colAmount') }}</Label>
            <div class="relative">
              <Input v-model="txnForm.amount" placeholder="1500,00" inputmode="decimal" class="pr-14" />
              <span class="absolute right-3 top-1/2 -translate-y-1/2 text-xs font-semibold text-muted-foreground">
                {{ accountCurrency }}
              </span>
            </div>
          </div>
        </div>
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">
            {{ txnForm.kind === 'transfer' ? t('finance.accountFrom') : t('finance.account') }}
          </Label>
          <Select v-model="txnForm.accountId" :options="accounts.map((a) => ({ value: a.id, label: `${a.name} · ${a.currency}` }))" />
        </div>
        <div v-if="txnForm.kind === 'transfer'" class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('finance.accountTo') }}</Label>
          <Select v-model="txnForm.accountToId" :options="accounts.map((a) => ({ value: a.id, label: `${a.name} · ${a.currency}` }))" />
        </div>
        <div v-if="txnForm.kind !== 'transfer'" class="grid grid-cols-2 gap-3">
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('finance.colCategory') }}</Label>
            <Select v-model="txnForm.categoryId" :options="[
              { value: '', label: '—' },
              ...categories.filter((c) => c.kind === txnForm.kind).map((c) => ({ value: c.id, label: c.name })),
            ]" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('finance.colCounterparty') }}</Label>
            <Select v-model="txnForm.counterpartyId" :options="[
              { value: '', label: '—' },
              ...counterparties.map((k) => ({ value: k.id, label: k.name })),
            ]" />
          </div>
        </div>
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('finance.colDescription') }}</Label>
          <Input v-model="txnForm.description" :placeholder="t('finance.descriptionPlaceholder')" />
        </div>

        <!-- Курс валюты: текущий + заморозка своего -->
        <div
          v-if="isForeign"
          class="rounded-xl border border-sky-200 bg-sky-50/60 p-3 dark:border-sky-900 dark:bg-sky-950/30"
        >
          <p class="text-xs leading-relaxed text-sky-800 dark:text-sky-300">
            {{ t('finance.currentRate') }}:
            <span class="font-semibold">{{ currentRate ? formatRate(currentRate) : t('finance.noRate') }}</span>
          </p>
          <label class="mt-2 flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
            <input v-model="freezeRate" type="checkbox" class="h-4 w-4 rounded border-zinc-300 accent-emerald-600 dark:border-zinc-700">
            {{ t('finance.freezeRate') }}
          </label>
          <div v-if="freezeRate" class="mt-2 max-w-[180px]">
            <Input v-model="freezeRateValue" :placeholder="t('finance.rateValuePlaceholder')" inputmode="decimal" />
          </div>
          <p class="mt-1.5 text-[11px] leading-snug text-muted-foreground">{{ t('finance.freezeRateHint') }}</p>
        </div>

        <label class="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
          <input v-model="txnForm.postImmediately" type="checkbox" class="h-4 w-4 rounded border-zinc-300 accent-emerald-600 dark:border-zinc-700">
          {{ t('finance.postImmediately') }}
        </label>

        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="txnOpen = false">{{ t('ui.cancel') }}</Button>
          <Button variant="emerald" type="submit" size="sm" :disabled="txnSaving">{{ t('finance.save') }}</Button>
        </div>
      </form>
    </Dialog>

    <!-- Диалог сторно -->
    <Dialog
      :open="stornoTarget !== null" :title="t('finance.stornoTitle')"
      @update:open="(v: boolean) => { if (!v) stornoTarget = null }"
    >
      <form class="space-y-4" @submit.prevent="doStorno">
        <p class="text-sm text-muted-foreground">
          {{ t('finance.stornoHint') }} <span class="font-semibold text-foreground">{{ stornoTarget?.doc_number }}</span>
        </p>
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('finance.stornoReason') }}</Label>
          <Input v-model="stornoReason" :placeholder="t('finance.stornoReasonPlaceholder')" />
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="stornoTarget = null">{{ t('ui.cancel') }}</Button>
          <Button variant="destructive" type="submit" size="sm" :disabled="stornoWorking">{{ t('finance.stornoConfirm') }}</Button>
        </div>
      </form>
    </Dialog>

    <!-- Диалоги справочников -->
    <Dialog :open="refDialog === 'account'" :title="t('finance.newAccount')" @update:open="(v: boolean) => { if (!v) refDialog = null }">
      <form class="space-y-4" @submit.prevent="saveReference">
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('finance.colName') }}</Label>
          <Input v-model="accountForm.name" :placeholder="t('finance.accountNamePlaceholder')" />
        </div>
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('finance.colCurrency') }}</Label>
          <Select v-model="accountForm.currency" :options="currencyOptions" />
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="refDialog = null">{{ t('ui.cancel') }}</Button>
          <Button variant="emerald" type="submit" size="sm" :disabled="refSaving || !accountForm.name.trim()">
            {{ t('finance.save') }}
          </Button>
        </div>
      </form>
    </Dialog>

    <Dialog :open="refDialog === 'category'" :title="t('finance.newCategory')" @update:open="(v: boolean) => { if (!v) refDialog = null }">
      <form class="space-y-4" @submit.prevent="saveReference">
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('finance.colName') }}</Label>
          <Input v-model="categoryForm.name" :placeholder="t('finance.categoryNamePlaceholder')" />
        </div>
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('finance.colKind') }}</Label>
          <Select v-model="categoryForm.kind" :options="[
            { value: 'expense', label: t('finance.kind.expense') },
            { value: 'income', label: t('finance.kind.income') },
          ]" />
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="refDialog = null">{{ t('ui.cancel') }}</Button>
          <Button variant="emerald" type="submit" size="sm" :disabled="refSaving || !categoryForm.name.trim()">
            {{ t('finance.save') }}
          </Button>
        </div>
      </form>
    </Dialog>

    <Dialog :open="refDialog === 'counterparty'" :title="t('finance.newCounterparty')" @update:open="(v: boolean) => { if (!v) refDialog = null }">
      <form class="space-y-4" @submit.prevent="saveReference">
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('finance.colName') }}</Label>
          <Input v-model="counterpartyForm.name" :placeholder="t('finance.counterpartyNamePlaceholder')" />
        </div>
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">ИНН</Label>
          <Input v-model="counterpartyForm.inn" placeholder="7707083893" />
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="refDialog = null">{{ t('ui.cancel') }}</Button>
          <Button variant="emerald" type="submit" size="sm" :disabled="refSaving || !counterpartyForm.name.trim()">
            {{ t('finance.save') }}
          </Button>
        </div>
      </form>
    </Dialog>
  </div>
</template>

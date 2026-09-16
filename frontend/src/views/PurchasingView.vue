<script setup lang="ts">
// Закупки (этап I): заказы поставщикам (создание со строками, подтверждение,
// оплата), приёмки (привязка к заказу, локации, для цифровых — список кодов;
// проведение/сторно), сальдо по поставщику. Кнопки — при accounting: rw.
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Ban, Check, PackageCheck, Plus, Wallet } from 'lucide-vue-next'
import { get, post } from '../api/client'
import {
  Badge, Button, Card, CardContent, Dialog, EmptyState, Input, Label,
  PaginatedList, Select, SearchSelect, Skeleton, Tabs, useToast,
} from '../components/ui'
import type { PageOf } from '../components/ui'
import { useAuthStore } from '../stores/auth'
import { formatMoney2, isPositiveDecimalString } from '../utils/money'

const { t, d } = useI18n()
const auth = useAuthStore()
const toast = useToast()
const canWrite = computed(() => auth.moduleLevel('accounting') === 'rw')

const tab = ref('orders')

interface PurchaseOrder {
  id: string; number: string | null; counterparty_id: string; status: string
  currency: string; rate: string | null; amount: string; amount_base: string | null
  note: string; created_at: string
  counterparty_name: string | null
}
interface Receipt {
  id: string; number: string | null; purchase_order_id: string | null
  counterparty_id: string; status: string; is_stornoed: boolean
  counterparty_doc: string | null; note: string; moved_at: string; created_at: string
  counterparty_name: string | null; purchase_order_number: string | null
}
interface Counterparty { id: string; name: string }
interface Item { id: string; sku: string; name: string; kind: string; tracking: string }
interface Location { id: string; name: string; kind: string; is_active: boolean }
interface Account { id: string; name: string; currency: string }

// списки пагинированы (по 50 + infinite scroll); имена контрагентов и
// номера заказов приходят в payload — полные справочники не грузим
const ordersList = ref<{ reload: () => Promise<void> } | null>(null)
const receiptsList = ref<{ reload: () => Promise<void> } | null>(null)
const fetchOrdersPage = (offset: number, limit: number) =>
  get<PageOf<PurchaseOrder>>(`/accounting/purchase-orders?limit=${limit}&offset=${offset}`)
const fetchReceiptsPage = (offset: number, limit: number) =>
  get<PageOf<Receipt>>(`/accounting/receipts?limit=${limit}&offset=${offset}`)

// справочники для диалогов/сальдо — лениво (диалог или вкладка «Сальдо»)
const counterparties = ref<Counterparty[]>([])
const items = ref<Item[]>([])
const locations = ref<Location[]>([])
const accounts = ref<Account[]>([])
const refsLoading = ref(false)

async function ensureDialogRefs() {
  if (counterparties.value.length || refsLoading.value) return
  refsLoading.value = true
  try {
    const [cp, it, loc, acc] = await Promise.all([
      get<Counterparty[]>('/accounting/counterparties'),
      get<Item[]>('/accounting/items?is_active=true'),
      get<Location[]>('/accounting/locations'),
      get<Account[]>('/accounting/accounts'),
    ])
    counterparties.value = cp
    items.value = it
    locations.value = loc
    accounts.value = acc
  } catch (error) {
    toast.apiError(error)
  } finally {
    refsLoading.value = false
  }
}

watch(tab, (value) => {
  if (value === 'balance') void ensureDialogRefs()
})

const cpName = (id: string) => counterparties.value.find((c) => c.id === id)?.name ?? '…'
const activeLocations = computed(() => locations.value.filter((l) => l.is_active))

// заказы для пикера приёмки — лениво при первом открытии диалога
const dialogOrders = ref<PurchaseOrder[]>([])
async function ensureDialogOrders() {
  if (dialogOrders.value.length) return
  try {
    dialogOrders.value = (await get<PageOf<PurchaseOrder>>('/accounting/purchase-orders?limit=0')).items
  } catch (error) {
    toast.apiError(error)
  }
}

function statusCls(status: string): string {
  return {
    draft: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
    confirmed: 'bg-sky-100 text-sky-800 dark:bg-sky-950/60 dark:text-sky-300',
    received: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300',
    posted: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300',
    partial: 'bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300',
    cancelled: 'bg-red-100 text-red-700 dark:bg-red-950/60 dark:text-red-300',
  }[status] ?? ''
}

// ---------- Заказ поставщику ----------
const orderOpen = ref(false)
const orderSaving = ref(false)
const orderForm = reactive({
  counterpartyId: '', currency: 'RUB', note: '',
  lines: [] as Array<{ itemId: string; qty: string; price: string }>,
})

function openOrderDialog() {
  void ensureDialogRefs()
  orderForm.counterpartyId = ''
  orderForm.currency = 'RUB'
  orderForm.note = ''
  orderForm.lines = [{ itemId: '', qty: '', price: '' }]
  orderOpen.value = true
}

async function saveOrder() {
  const lines = orderForm.lines
    .filter((l) => l.itemId && isPositiveDecimalString(l.qty) && isPositiveDecimalString(l.price))
  if (orderSaving.value || !orderForm.counterpartyId || !lines.length) return
  orderSaving.value = true
  try {
    await post('/accounting/purchase-orders', {
      counterparty_id: orderForm.counterpartyId,
      currency: orderForm.currency,
      note: orderForm.note,
      lines: lines.map((l) => ({
        item_id: l.itemId,
        qty: l.qty.trim().replace(',', '.'),
        unit_price: l.price.trim().replace(',', '.'),
      })),
    })
    toast.success(t('pur.orderCreated'))
    orderOpen.value = false
    dialogOrders.value = []
    await ordersList.value?.reload()
  } catch (error) {
    toast.apiError(error)
  } finally {
    orderSaving.value = false
  }
}

async function confirmOrder(order: PurchaseOrder) {
  try {
    await post(`/accounting/purchase-orders/${order.id}/confirm`)
    toast.success(t('pur.orderConfirmed'))
    await ordersList.value?.reload()
  } catch (error) {
    toast.apiError(error)
  }
}

// ---------- Оплата ----------
const payTarget = ref<'purchase' | null>(null)
const payOrder = ref<PurchaseOrder | null>(null)
const payForm = reactive({ accountId: '', amount: '' })

function openPay(order: PurchaseOrder) {
  void ensureDialogRefs()
  payOrder.value = order
  payForm.accountId = accounts.value.find((a) => a.currency === 'RUB')?.id ?? ''
  payForm.amount = order.amount_base ?? order.amount
  payTarget.value = 'purchase'
}

async function savePay() {
  if (!payOrder.value || !isPositiveDecimalString(payForm.amount)) return
  try {
    await post(`/accounting/purchase-orders/${payOrder.value.id}/pay`, {
      account_id: payForm.accountId,
      amount: payForm.amount.trim().replace(',', '.'),
    })
    toast.success(t('pur.paid'))
    payTarget.value = null
    await ordersList.value?.reload()
  } catch (error) {
    toast.apiError(error)
  }
}

// ---------- Приёмка ----------
const receiptOpen = ref(false)
const receiptSaving = ref(false)
const receiptForm = reactive({
  orderId: '', counterpartyDoc: '',
  lines: [] as Array<{ itemId: string; qty: string; unitCost: string; locationId: string; codes: string }>,
})

function openReceiptDialog(order?: PurchaseOrder) {
  void ensureDialogRefs()
  void ensureDialogOrders()
  receiptForm.orderId = order?.id ?? ''
  receiptForm.counterpartyDoc = ''
  const prefill = order
    ? []
    : [{ itemId: '', qty: '', unitCost: '', locationId: activeLocations.value[0]?.id ?? '', codes: '' }]
  receiptForm.lines = prefill.length ? prefill : [{ itemId: '', qty: '', unitCost: '', locationId: activeLocations.value[0]?.id ?? '', codes: '' }]
  receiptOpen.value = true
}

async function saveReceipt() {
  const lines = receiptForm.lines
    .filter((l) => l.itemId && isPositiveDecimalString(l.qty))
    .map((l) => {
      const item = items.value.find((i) => i.id === l.itemId)
      if (item?.tracking === 'serial') {
        return {
          item_id: l.itemId,
          qty: l.qty.trim().replace(',', '.'),
          location_id: l.locationId || null,
          serial_codes: l.codes.split(/[\n,;]+/).map((c) => c.trim()).filter(Boolean),
        }
      }
      return {
        item_id: l.itemId,
        qty: l.qty.trim().replace(',', '.'),
        unit_cost: l.unitCost.trim() ? l.unitCost.trim().replace(',', '.') : null,
        location_id: l.locationId || null,
      }
    })
  if (receiptSaving.value || !lines.length) return
  receiptSaving.value = true
  try {
    await post('/accounting/receipts', {
      purchase_order_id: receiptForm.orderId || null,
      counterparty_doc: receiptForm.counterpartyDoc || null,
      lines,
    })
    toast.success(t('pur.receiptCreated'))
    receiptOpen.value = false
    dialogOrders.value = []
    await Promise.all([
      receiptsList.value?.reload() ?? Promise.resolve(),
      ordersList.value?.reload() ?? Promise.resolve(),
    ])
  } catch (error) {
    toast.apiError(error)
  } finally {
    receiptSaving.value = false
  }
}

async function postReceipt(receipt: Receipt) {
  try {
    await post(`/accounting/receipts/${receipt.id}/post`)
    toast.success(t('pur.receiptPosted'))
    await Promise.all([
      receiptsList.value?.reload() ?? Promise.resolve(),
      ordersList.value?.reload() ?? Promise.resolve(),
    ])
  } catch (error) {
    toast.apiError(error)
  }
}

async function unpostReceipt(receipt: Receipt) {
  try {
    await post(`/accounting/receipts/${receipt.id}/unpost`, { reason: t('pur.stornoReason') })
    toast.success(t('pur.receiptUnposted'))
    await Promise.all([
      receiptsList.value?.reload() ?? Promise.resolve(),
      ordersList.value?.reload() ?? Promise.resolve(),
    ])
  } catch (error) {
    toast.apiError(error)
  }
}

// ---------- Сальдо ----------
const balanceCp = ref('')
const balanceRows = ref<Array<{ date: string; amount: string; description: string }>>([])
const balanceTotal = ref('0')
const balanceLoading = ref(false)

async function loadBalance() {
  if (!balanceCp.value) return
  balanceLoading.value = true
  try {
    const data = await get<{ total: string; rows: Array<{ date: string; amount: string; description: string }> }>(
      `/accounting/reports/counterparty-balance?counterparty_id=${balanceCp.value}`)
    balanceTotal.value = data.total
    balanceRows.value = data.rows ?? []
  } catch (error) {
    toast.apiError(error)
  } finally {
    balanceLoading.value = false
  }
}
</script>

<template>
  <div class="space-y-4">
    <div>
      <h2 class="text-lg font-bold tracking-tight">{{ t('pur.title') }}</h2>
      <p class="text-sm text-muted-foreground">{{ t('pur.subtitle') }}</p>
    </div>

    <Tabs
      :tabs="[
        { key: 'orders', label: t('pur.tabOrders') },
        { key: 'receipts', label: t('pur.tabReceipts') },
        { key: 'balance', label: t('pur.tabBalance') },
      ]"
      :model-value="tab"
      @update:model-value="tab = $event"
    />

    <!-- Заказы -->
    <div v-if="tab === 'orders'" class="space-y-3">
      <PaginatedList ref="ordersList" :fetch-page="fetchOrdersPage" v-slot="{ items: orderRows, loading }">
      <div class="flex items-center justify-between">
        <span />
        <Button v-if="canWrite" variant="emerald" size="sm" class="gap-1.5" @click="openOrderDialog">
          <Plus class="h-3.5 w-3.5" /> {{ t('pur.newOrder') }}
        </Button>
      </div>
      <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="p-0">
          <div v-if="loading" class="space-y-2 p-4"><Skeleton class="h-10 w-full" /><Skeleton class="h-10 w-full" /></div>
          <div v-else-if="!orderRows.length" class="p-6"><EmptyState :title="t('ui.emptyTitle')" :description="t('ui.emptyDescription')" /></div>
          <div v-else class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                  <th class="px-3 py-2 font-medium">№</th>
                  <th class="px-3 py-2 font-medium">{{ t('pur.colSupplier') }}</th>
                  <th class="px-3 py-2 text-right font-medium">{{ t('pur.colAmount') }}</th>
                  <th class="hidden px-3 py-2 font-medium sm:table-cell">{{ t('pur.colDate') }}</th>
                  <th class="px-3 py-2 font-medium">{{ t('pur.colStatus') }}</th>
                  <th v-if="canWrite" class="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                <tr v-for="order in orderRows" :key="order.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
                  <td class="whitespace-nowrap px-3 py-2 font-medium text-emerald-700 dark:text-emerald-400">{{ order.number ?? '—' }}</td>
                  <td class="px-3 py-2">{{ order.counterparty_name ?? '…' }}</td>
                  <td class="whitespace-nowrap px-3 py-2 text-right font-semibold">{{ formatMoney2(order.amount, order.currency) }}
                    <span v-if="order.amount_base && order.currency !== 'RUB'" class="block text-[10px] font-normal text-muted-foreground">{{ formatMoney2(order.amount_base) }}</span>
                  </td>
                  <td class="hidden whitespace-nowrap px-3 py-2 text-xs text-muted-foreground sm:table-cell">{{ d(order.created_at, 'short') }}</td>
                  <td class="px-3 py-2"><Badge :class="statusCls(order.status)">{{ t(`pur.status.${order.status}`) }}</Badge></td>
                  <td v-if="canWrite" class="px-3 py-2">
                    <div class="flex justify-end gap-1">
                      <Button v-if="order.status === 'draft'" variant="ghost" size="icon" class="h-7 w-7" :title="t('pur.confirm')" @click="confirmOrder(order)">
                        <Check class="h-3.5 w-3.5 text-emerald-600" />
                      </Button>
                      <Button v-if="order.status !== 'draft' && order.status !== 'cancelled'" variant="ghost" size="icon" class="h-7 w-7" :title="t('pur.pay')" @click="openPay(order)">
                        <Wallet class="h-3.5 w-3.5 text-sky-600" />
                      </Button>
                      <Button v-if="order.status !== 'draft'" variant="ghost" size="icon" class="h-7 w-7" :title="t('pur.receive')" @click="openReceiptDialog(order)">
                        <PackageCheck class="h-3.5 w-3.5 text-amber-600" />
                      </Button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
      </PaginatedList>
    </div>

    <!-- Приёмки -->
    <div v-else-if="tab === 'receipts'" class="space-y-3">
      <PaginatedList ref="receiptsList" :fetch-page="fetchReceiptsPage" v-slot="{ items: receiptRows, loading }">
      <div class="flex items-center justify-between">
        <span />
        <Button v-if="canWrite" variant="emerald" size="sm" class="gap-1.5" @click="openReceiptDialog()">
          <Plus class="h-3.5 w-3.5" /> {{ t('pur.newReceipt') }}
        </Button>
      </div>
      <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="p-0">
          <div v-if="loading" class="space-y-2 p-4"><Skeleton class="h-10 w-full" /></div>
          <div v-else-if="!receiptRows.length" class="p-6"><EmptyState :title="t('ui.emptyTitle')" :description="t('ui.emptyDescription')" /></div>
          <div v-else class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                  <th class="px-3 py-2 font-medium">№</th>
                  <th class="px-3 py-2 font-medium">{{ t('pur.colOrder') }}</th>
                  <th class="hidden px-3 py-2 font-medium sm:table-cell">{{ t('pur.colSupplier') }}</th>
                  <th class="hidden px-3 py-2 font-medium md:table-cell">{{ t('pur.colDoc') }}</th>
                  <th class="px-3 py-2 font-medium">{{ t('pur.colStatus') }}</th>
                  <th v-if="canWrite" class="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                <tr v-for="receipt in receiptRows" :key="receipt.id"
                    class="border-t border-zinc-100 dark:border-zinc-800/70" :class="receipt.is_stornoed && 'opacity-60'">
                  <td class="whitespace-nowrap px-3 py-2 font-medium text-emerald-700 dark:text-emerald-400">{{ receipt.number ?? '—' }}</td>
                  <td class="px-3 py-2">{{ receipt.purchase_order_number ?? '—' }}</td>
                  <td class="hidden px-3 py-2 text-muted-foreground sm:table-cell">{{ receipt.counterparty_name ?? '…' }}</td>
                  <td class="hidden px-3 py-2 text-xs text-muted-foreground md:table-cell">{{ receipt.counterparty_doc ?? '—' }}</td>
                  <td class="px-3 py-2">
                    <Badge :class="receipt.is_stornoed ? 'bg-red-100 text-red-700 dark:bg-red-950/60 dark:text-red-300' : statusCls(receipt.status)">
                      {{ receipt.is_stornoed ? t('pur.status.stornoed') : t(`pur.status.${receipt.status}`) }}
                    </Badge>
                  </td>
                  <td v-if="canWrite" class="px-3 py-2">
                    <div class="flex justify-end gap-1">
                      <Button v-if="receipt.status === 'draft'" variant="ghost" size="icon" class="h-7 w-7" :title="t('pur.post')" @click="postReceipt(receipt)">
                        <Check class="h-3.5 w-3.5 text-emerald-600" />
                      </Button>
                      <Button v-else-if="receipt.status === 'posted' && !receipt.is_stornoed" variant="ghost" size="icon" class="h-7 w-7" :title="t('pur.unpost')" @click="unpostReceipt(receipt)">
                        <Ban class="h-3.5 w-3.5 text-amber-600" />
                      </Button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
      </PaginatedList>
    </div>

    <!-- Сальдо -->
    <div v-else class="space-y-3">
      <div class="flex items-end gap-2">
        <div class="w-[300px] space-y-1">
          <Label class="text-xs text-muted-foreground">{{ t('pur.colSupplier') }}</Label>
          <Select v-model="balanceCp" :options="counterparties.map((c) => ({ value: c.id, label: c.name }))" @update:model-value="loadBalance" />
        </div>
        <span v-if="balanceCp" class="text-sm text-muted-foreground">
          {{ t('pur.balanceTotal') }}: <span class="font-semibold text-foreground">{{ formatMoney2(balanceTotal) }}</span>
        </span>
      </div>
      <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="p-0">
          <div v-if="balanceLoading" class="space-y-2 p-4"><Skeleton class="h-8 w-full" /></div>
          <div v-else-if="!balanceCp" class="p-6"><EmptyState :title="t('ui.emptyTitle')" :description="t('pur.pickSupplier')" /></div>
          <div v-else-if="balanceRows.length === 0" class="p-6"><EmptyState :title="t('ui.emptyTitle')" :description="t('ui.emptyDescription')" /></div>
          <div v-else class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                  <th class="px-3 py-2 font-medium">{{ t('pur.colDate') }}</th>
                  <th class="px-3 py-2 font-medium">{{ t('pur.colDescription') }}</th>
                  <th class="px-3 py-2 text-right font-medium">{{ t('pur.colAmount') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, i) in balanceRows" :key="i" class="border-t border-zinc-100 dark:border-zinc-800/70">
                  <td class="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">{{ row.date }}</td>
                  <td class="px-3 py-2">{{ row.description || '—' }}</td>
                  <td class="whitespace-nowrap px-3 py-2 text-right font-semibold tabular-nums">{{ formatMoney2(row.amount) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>

    <!-- Диалог: заказ поставщику -->
    <Dialog v-model:open="orderOpen" :title="t('pur.newOrder')" width="640px">
      <form class="space-y-4" @submit.prevent="saveOrder">
        <div class="grid grid-cols-2 gap-3">
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('pur.colSupplier') }}</Label>
            <SearchSelect v-model="orderForm.counterpartyId" :options="counterparties.map((c) => ({ value: c.id, label: c.name }))" :search-placeholder="t('ui.searchPlaceholder')" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('finance.colCurrency') }}</Label>
            <Select v-model="orderForm.currency" :options="[
              { value: 'RUB', label: 'RUB ₽' }, { value: 'USD', label: 'USD $' },
              { value: 'EUR', label: 'EUR €' }, { value: 'CNY', label: 'CNY ¥' },
            ]" />
          </div>
        </div>
        <p class="text-xs font-medium text-muted-foreground">{{ t('pur.lines') }}</p>
        <div v-for="(line, index) in orderForm.lines" :key="index" class="flex items-end gap-2">
          <div class="flex-1 space-y-1">
            <SearchSelect v-model="line.itemId" :options="items.map((i) => ({ value: i.id, label: `${i.sku} · ${i.name}` }))" />
          </div>
          <div class="w-24 space-y-1"><Input v-model="line.qty" :placeholder="t('inv.phQty')" inputmode="decimal" /></div>
          <div class="w-28 space-y-1"><Input v-model="line.price" :placeholder="t('inv.phPrice')" inputmode="decimal" /></div>
          <Button variant="ghost" size="icon" class="h-9 w-9" @click="orderForm.lines.splice(index, 1)">✕</Button>
        </div>
        <Button variant="outline" size="sm" class="gap-1.5" @click="orderForm.lines.push({ itemId: '', qty: '', price: '' })">
          <Plus class="h-3.5 w-3.5" /> {{ t('inv.addLine') }}
        </Button>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="orderOpen = false">{{ t('ui.cancel') }}</Button>
          <Button variant="emerald" type="submit" size="sm" :disabled="orderSaving">{{ t('ui.save') }}</Button>
        </div>
      </form>
    </Dialog>

    <!-- Диалог: оплата -->
    <Dialog
      :open="payTarget !== null" :title="t('pur.pay')"
      @update:open="(v: boolean) => { if (!v) payTarget = null }"
    >
      <form class="space-y-4" @submit.prevent="savePay">
        <p v-if="payOrder" class="text-sm text-muted-foreground">
          {{ payOrder.number }} · {{ payOrder.counterparty_name ?? '…' }} · {{ formatMoney2(payOrder.amount, payOrder.currency) }}
        </p>
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('finance.account') }}</Label>
          <SearchSelect v-model="payForm.accountId" :options="accounts.map((a) => ({ value: a.id, label: `${a.name} · ${a.currency}` }))" :search-placeholder="t('ui.searchPlaceholder')" />
        </div>
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('pur.payAmount') }}</Label>
          <Input v-model="payForm.amount" inputmode="decimal" />
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="payTarget = null">{{ t('ui.cancel') }}</Button>
          <Button variant="emerald" type="submit" size="sm">{{ t('pur.pay') }}</Button>
        </div>
      </form>
    </Dialog>

    <!-- Диалог: приёмка -->
    <Dialog v-model:open="receiptOpen" :title="t('pur.newReceipt')" width="680px">
      <form class="space-y-4" @submit.prevent="saveReceipt">
        <div class="grid grid-cols-2 gap-3">
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('pur.colOrder') }}</Label>
            <SearchSelect v-model="receiptForm.orderId" :options="[
              { value: '', label: '—' },
              ...dialogOrders.filter((o) => o.status !== 'cancelled').map((o) => ({ value: o.id, label: `${o.number ?? o.id.slice(0, 8)} · ${o.counterparty_name ?? '…'}` })),
            ]" :search-placeholder="t('ui.searchPlaceholder')" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('pur.colDoc') }}</Label>
            <Input v-model="receiptForm.counterpartyDoc" placeholder="накладная №45" />
          </div>
        </div>
        <p class="text-xs font-medium text-muted-foreground">{{ t('pur.lines') }}</p>
        <div v-for="(line, index) in receiptForm.lines" :key="index" class="space-y-1.5 rounded-lg border border-zinc-200 p-2.5 dark:border-zinc-800">
          <div class="flex items-end gap-2">
            <div class="flex-1 space-y-1">
              <SearchSelect v-model="line.itemId" :options="items.map((i) => ({ value: i.id, label: `${i.sku} · ${i.name}` }))" />
            </div>
            <div class="w-24 space-y-1"><Input v-model="line.qty" :placeholder="t('inv.phQty')" inputmode="decimal" /></div>
            <div class="w-32 space-y-1"><Input v-model="line.unitCost" :placeholder="t('inv.phCost')" inputmode="decimal" /></div>
            <Button variant="ghost" size="icon" class="h-9 w-9" @click="receiptForm.lines.splice(index, 1)">✕</Button>
          </div>
          <div class="flex items-end gap-2">
            <div class="w-56 space-y-1">
              <SearchSelect v-model="line.locationId" :options="activeLocations.map((l) => ({ value: l.id, label: l.name }))" />
            </div>
            <div v-if="items.find((i) => i.id === line.itemId)?.tracking === 'serial'" class="flex-1 space-y-1">
              <textarea
                v-model="line.codes" rows="1" :placeholder="t('inv.codesPlaceholder')"
                class="w-full rounded-lg border border-input bg-background px-3 py-2 text-sm shadow-sm"
              />
            </div>
          </div>
        </div>
        <Button variant="outline" size="sm" class="gap-1.5" @click="receiptForm.lines.push({ itemId: '', qty: '', unitCost: '', locationId: activeLocations[0]?.id ?? '', codes: '' })">
          <Plus class="h-3.5 w-3.5" /> {{ t('inv.addLine') }}
        </Button>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="receiptOpen = false">{{ t('ui.cancel') }}</Button>
          <Button variant="emerald" type="submit" size="sm" :disabled="receiptSaving">{{ t('ui.save') }}</Button>
        </div>
      </form>
    </Dialog>
  </div>
</template>

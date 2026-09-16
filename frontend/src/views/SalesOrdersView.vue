<script setup lang="ts">
// Продажи-документы (этап I, стр. 4–5): заказы клиентов — таблица со
// статусами и суммами, карточка (строки с резервами, отгрузки с проведением/
// сторно, оплаты, история record_versions), создание заказа — со вкладки
// сделок из выигранной (префилл контрагента через crm_deal_id) или вручную.
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { Ban, Check, Plus, Truck, Wallet } from 'lucide-vue-next'
import { get, post } from '../api/client'
import {
  Badge, Button, Card, CardContent, Dialog, EmptyState, Input, Label,
  PaginatedList, Select, SearchSelect, Skeleton, useToast,
} from '../components/ui'
import type { PageOf } from '../components/ui'
import { useAuthStore } from '../stores/auth'
import { formatMoney2, isPositiveDecimalString } from '../utils/money'

const { t, d } = useI18n()
const route = useRoute()
const auth = useAuthStore()
const toast = useToast()
const canWrite = computed(() => auth.moduleLevel('accounting') === 'rw')

interface SalesOrder {
  id: string; number: string | null; counterparty_id: string | null
  crm_deal_id: string | null; status: string; currency: string
  rate: string | null; amount: string; amount_base: string | null
  note: string; created_at: string
  lines?: Array<{ id: string; item_id: string; qty: string; unit_price: string; amount: string; reserved_qty: string }>
  counterparty_name?: string | null
}
interface Shipment {
  id: string; number: string | null; sales_order_id: string; status: string
  is_stornoed: boolean; moved_at: string
  lines: Array<{ id: string; item_id: string; qty: string; unit_price: string; serial_codes: string[] | null }>
}
interface Counterparty { id: string; name: string }
interface Item { id: string; sku: string; name: string; tracking: string }
interface Location { id: string; name: string; kind: string; is_active: boolean }
interface Account { id: string; name: string; currency: string }
interface HistoryRow {
  changed_at: string
  diff: Record<string, { old: unknown; new: unknown }>
}

// список заказов пагинирован (по 50 + infinite scroll); имена
// контрагентов приходят в payload; справочники для диалогов/карточки —
// лениво, экран по умолчанию их не грузит
const ordersList = ref<{ reload: () => Promise<void> } | null>(null)
const fetchOrdersPage = (offset: number, limit: number) =>
  get<PageOf<SalesOrder>>(`/accounting/sales-orders?limit=${limit}&offset=${offset}`)

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

const cpName = (id: string | null) =>
  id ? (counterparties.value.find((c) => c.id === id)?.name ?? '…') : '—'
const itemName = (id: string) => items.value.find((i) => i.id === id)?.name ?? '…'
const activeLocations = computed(() => locations.value.filter((l) => l.is_active && !l.kind.startsWith('transit')))

onMounted(async () => {
  // создание из выигранной сделки: /crm/orders?deal=<id>&counterparty=<id>
  const deal = route.query.deal
  if (deal && canWrite.value) void openCreate(String(deal), route.query.counterparty ? String(route.query.counterparty) : '')
})

function statusCls(status: string): string {
  return {
    draft: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
    confirmed: 'bg-sky-100 text-sky-800 dark:bg-sky-950/60 dark:text-sky-300',
    shipped: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300',
    completed: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300',
    cancelled: 'bg-red-100 text-red-700 dark:bg-red-950/60 dark:text-red-300',
  }[status] ?? ''
}

// ---------- Создание заказа ----------
const createOpen = ref(false)
const creating = ref(false)
const createForm = reactive({
  counterpartyId: '', dealId: '', note: '',
  lines: [] as Array<{ itemId: string; qty: string; price: string }>,
})

function openCreate(dealId?: string, counterpartyId?: string) {
  void ensureDialogRefs()
  createForm.dealId = dealId ?? ''
  createForm.counterpartyId = counterpartyId ?? ''
  createForm.note = ''
  createForm.lines = [{ itemId: '', qty: '', price: '' }]
  createOpen.value = true
}

async function saveOrder() {
  const lines = createForm.lines
    .filter((l) => l.itemId && isPositiveDecimalString(l.qty) && isPositiveDecimalString(l.price))
  if (creating.value || !lines.length || (!createForm.counterpartyId && !createForm.dealId)) return
  creating.value = true
  try {
    await post('/accounting/sales-orders', {
      counterparty_id: createForm.counterpartyId || null,
      crm_deal_id: createForm.dealId || null,
      note: createForm.note,
      lines: lines.map((l) => ({
        item_id: l.itemId,
        qty: l.qty.trim().replace(',', '.'),
        unit_price: l.price.trim().replace(',', '.'),
      })),
    })
    toast.success(t('sal.orderCreated'))
    createOpen.value = false
    await ordersList.value?.reload()
  } catch (error) {
    toast.apiError(error)
  } finally {
    creating.value = false
  }
}

async function confirmOrder(order: SalesOrder) {
  try {
    await post(`/accounting/sales-orders/${order.id}/confirm`)
    toast.success(t('sal.orderConfirmed'))
    await ordersList.value?.reload()
    if (card.value?.id === order.id) await openCard(order.id)
  } catch (error) {
    toast.apiError(error)
  }
}

// ---------- Оплата ----------
const payOpen = ref(false)
const payOrder = ref<SalesOrder | null>(null)
const payForm = reactive({ accountId: '', amount: '' })

function openPay(order: SalesOrder) {
  void ensureDialogRefs()
  payOrder.value = order
  payForm.accountId = accounts.value.find((a) => a.currency === 'RUB')?.id ?? ''
  payForm.amount = order.amount_base ?? order.amount
  payOpen.value = true
}

async function savePay() {
  if (!payOrder.value || !isPositiveDecimalString(payForm.amount)) return
  try {
    await post(`/accounting/sales-orders/${payOrder.value.id}/pay`, {
      account_id: payForm.accountId,
      amount: payForm.amount.trim().replace(',', '.'),
    })
    toast.success(t('sal.paid'))
    payOpen.value = false
    await ordersList.value?.reload()
  } catch (error) {
    toast.apiError(error)
  }
}

// ---------- Карточка ----------
const card = ref<SalesOrder | null>(null)
const cardShipments = ref<Shipment[]>([])
const cardHistory = ref<HistoryRow[]>([])

async function openCard(id: string) {
  void ensureDialogRefs()
  const [order, shipments, history] = await Promise.all([
    get<SalesOrder>(`/accounting/sales-orders/${id}`),
    get<PageOf<Shipment>>(`/accounting/shipments?sales_order_id=${id}&limit=0`),
    get<HistoryRow[]>(`/accounting/history/acc.sales.order/${id}`),
  ])
  card.value = order
  cardShipments.value = shipments.items
  cardHistory.value = history
}

// ---------- Отгрузка ----------
const shipOpen = ref(false)
const shipSaving = ref(false)
const shipForm = reactive({
  lines: [] as Array<{ itemId: string; qty: string; locationId: string; codes: string }>,
})

function openShip(order: SalesOrder) {
  void ensureDialogRefs()
  const lines = order.lines ?? []
  shipForm.lines = lines.map((line) => ({
    itemId: line.item_id, qty: line.qty,
    locationId: activeLocations.value[0]?.id ?? '', codes: '',
  }))
  if (!shipForm.lines.length) {
    shipForm.lines = [{ itemId: '', qty: '', locationId: activeLocations.value[0]?.id ?? '', codes: '' }]
  }
  shipOpen.value = true
}

async function saveShipment() {
  if (!card.value) return
  const lines = shipForm.lines
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
        location_id: l.locationId || null,
      }
    })
  if (shipSaving.value || !lines.length) return
  shipSaving.value = true
  try {
    await post('/accounting/shipments', {
      sales_order_id: card.value.id,
      lines,
    })
    toast.success(t('sal.shipmentCreated'))
    shipOpen.value = false
    await Promise.all([ordersList.value?.reload() ?? Promise.resolve(), openCard(card.value.id)])
  } catch (error) {
    toast.apiError(error)
  } finally {
    shipSaving.value = false
  }
}

async function postShipment(shipment: Shipment) {
  try {
    await post(`/accounting/shipments/${shipment.id}/post`)
    toast.success(t('sal.shipmentPosted'))
    if (card.value) await Promise.all([ordersList.value?.reload() ?? Promise.resolve(), openCard(card.value.id)])
  } catch (error) {
    toast.apiError(error)
  }
}

async function unpostShipment(shipment: Shipment) {
  try {
    await post(`/accounting/shipments/${shipment.id}/unpost`, { reason: t('sal.stornoReason') })
    toast.success(t('sal.shipmentUnposted'))
    if (card.value) await Promise.all([ordersList.value?.reload() ?? Promise.resolve(), openCard(card.value.id)])
  } catch (error) {
    toast.apiError(error)
  }
}

function historyLine(diff: Record<string, { old: unknown; new: unknown }>): string {
  return Object.entries(diff)
    .map(([field, change]) => `${field}: ${String(change.old ?? '—')} → ${String(change.new ?? '—')}`)
    .join('; ')
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div>
        <h2 class="text-lg font-bold tracking-tight">{{ t('sal.title') }}</h2>
        <p class="text-sm text-muted-foreground">{{ t('sal.subtitle') }}</p>
      </div>
      <Button v-if="canWrite" variant="emerald" size="sm" class="gap-1.5" @click="openCreate()">
        <Plus class="h-4 w-4" /> {{ t('sal.newOrder') }}
      </Button>
    </div>

    <PaginatedList ref="ordersList" :fetch-page="fetchOrdersPage" v-slot="{ items: orderRows, loading }">
    <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-0">
        <div v-if="loading" class="space-y-2 p-4">
          <Skeleton class="h-10 w-full" /><Skeleton class="h-10 w-full" /><Skeleton class="h-10 w-full" />
        </div>
        <div v-else-if="orderRows.length === 0" class="p-6">
          <EmptyState :title="t('ui.emptyTitle')" :description="t('ui.emptyDescription')" />
        </div>
        <div v-else class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                <th class="px-3 py-2 font-medium">№</th>
                <th class="px-3 py-2 font-medium">{{ t('sal.colCustomer') }}</th>
                <th class="px-3 py-2 text-right font-medium">{{ t('sal.colAmount') }}</th>
                <th class="hidden px-3 py-2 font-medium sm:table-cell">{{ t('sal.colDate') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('sal.colStatus') }}</th>
                <th v-if="canWrite" class="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="order in orderRows" :key="order.id"
                class="cursor-pointer border-t border-zinc-100 transition-colors hover:bg-zinc-50/60 dark:border-zinc-800/70 dark:hover:bg-zinc-800/40"
                @click="openCard(order.id)"
              >
                <td class="whitespace-nowrap px-3 py-2 font-medium text-emerald-700 dark:text-emerald-400">{{ order.number ?? '—' }}</td>
                <td class="px-3 py-2">
                  {{ order.counterparty_name ?? '…' }}
                  <Badge v-if="order.crm_deal_id" variant="outline" class="ml-1.5 text-[10px]">CRM</Badge>
                </td>
                <td class="whitespace-nowrap px-3 py-2 text-right font-semibold">
                  {{ formatMoney2(order.amount, order.currency) }}
                  <span v-if="order.amount_base && order.currency !== 'RUB'" class="block text-[10px] font-normal text-muted-foreground">{{ formatMoney2(order.amount_base) }}</span>
                </td>
                <td class="hidden whitespace-nowrap px-3 py-2 text-xs text-muted-foreground sm:table-cell">{{ d(order.created_at, 'short') }}</td>
                <td class="px-3 py-2"><Badge :class="statusCls(order.status)">{{ t(`sal.status.${order.status}`) }}</Badge></td>
                <td v-if="canWrite" class="px-3 py-2" @click.stop>
                  <div class="flex justify-end gap-1">
                    <Button v-if="order.status === 'draft'" variant="ghost" size="icon" class="h-7 w-7" :title="t('sal.confirm')" @click="confirmOrder(order)">
                      <Check class="h-3.5 w-3.5 text-emerald-600" />
                    </Button>
                    <Button v-else-if="order.status === 'confirmed'" variant="ghost" size="icon" class="h-7 w-7" :title="t('sal.pay')" @click="openPay(order)">
                      <Wallet class="h-3.5 w-3.5 text-sky-600" />
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

    <!-- Карточка заказа -->
    <Dialog
      :open="card !== null" :title="card?.number ?? t('sal.title')" width="720px"
      @update:open="(v: boolean) => { if (!v) card = null }"
    >
      <div v-if="card" class="space-y-4">
        <div class="flex flex-wrap items-center gap-2">
          <Badge :class="statusCls(card.status)">{{ t(`sal.status.${card.status}`) }}</Badge>
          <span class="text-sm font-semibold">{{ formatMoney2(card.amount, card.currency) }}</span>
          <span v-if="card.amount_base" class="text-xs text-muted-foreground">≈ {{ formatMoney2(card.amount_base) }}</span>
          <span class="ml-auto text-xs text-muted-foreground">{{ cpName(card.counterparty_id) }} · {{ d(card.created_at, 'short') }}</span>
        </div>

        <!-- строки -->
        <div class="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                <th class="px-3 py-2 font-medium">{{ t('sal.colItem') }}</th>
                <th class="px-3 py-2 text-right font-medium">{{ t('sal.colQty') }}</th>
                <th class="px-3 py-2 text-right font-medium">{{ t('sal.colPrice') }}</th>
                <th class="px-3 py-2 text-right font-medium">{{ t('sal.colAmount') }}</th>
                <th class="px-3 py-2 text-right font-medium">{{ t('sal.colReserved') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="line in card.lines ?? []" :key="line.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
                <td class="px-3 py-2">{{ itemName(line.item_id) }}</td>
                <td class="px-3 py-2 text-right tabular-nums">{{ Number(line.qty).toLocaleString('ru-RU') }}</td>
                <td class="px-3 py-2 text-right tabular-nums">{{ formatMoney2(line.unit_price) }}</td>
                <td class="px-3 py-2 text-right font-semibold tabular-nums">{{ formatMoney2(line.amount) }}</td>
                <td class="px-3 py-2 text-right text-xs text-muted-foreground tabular-nums">{{ Number(line.reserved_qty).toLocaleString('ru-RU') }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- отгрузки -->
        <div class="rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
          <div class="flex items-center justify-between">
            <p class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{{ t('sal.shipments') }} ({{ cardShipments.length }})</p>
            <div v-if="canWrite && (card.status === 'confirmed' || card.status === 'shipped')" class="flex gap-2">
              <Button variant="outline" size="sm" class="gap-1.5" @click="openShip(card)">
                <Truck class="h-3.5 w-3.5" /> {{ t('sal.ship') }}
              </Button>
              <Button variant="outline" size="sm" @click="openPay(card)">{{ t('sal.pay') }}</Button>
            </div>
          </div>
          <ul class="mt-2 space-y-1.5">
            <li v-if="cardShipments.length === 0" class="text-sm text-muted-foreground">{{ t('sal.noShipments') }}</li>
            <li v-for="shipment in cardShipments" :key="shipment.id"
                class="flex items-center justify-between rounded-lg border border-zinc-200 px-3 py-2 text-sm dark:border-zinc-800"
                :class="shipment.is_stornoed && 'opacity-60'">
              <span>
                {{ shipment.number }}
                <span class="ml-2 text-xs text-muted-foreground">{{ shipment.moved_at }}</span>
              </span>
              <span class="flex items-center gap-2">
                <Badge :class="shipment.is_stornoed ? 'bg-red-100 text-red-700 dark:bg-red-950/60 dark:text-red-300' : statusCls(shipment.status)">
                  {{ shipment.is_stornoed ? t('sal.status.stornoed') : t(`sal.status.${shipment.status}`) }}
                </Badge>
                <template v-if="canWrite && !shipment.is_stornoed">
                  <Button v-if="shipment.status === 'draft'" variant="ghost" size="icon" class="h-6 w-6" :title="t('sal.postShipment')" @click="postShipment(shipment)">
                    <Check class="h-3 w-3 text-emerald-600" />
                  </Button>
                  <Button v-else-if="shipment.status === 'posted'" variant="ghost" size="icon" class="h-6 w-6" :title="t('sal.unpostShipment')" @click="unpostShipment(shipment)">
                    <Ban class="h-3 w-3 text-amber-600" />
                  </Button>
                </template>
              </span>
            </li>
          </ul>
        </div>

        <!-- история -->
        <details class="group rounded-xl border border-zinc-200 px-4 py-3 dark:border-zinc-800">
          <summary class="cursor-pointer list-none text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {{ t('crm.history') }} ({{ cardHistory.length }})
          </summary>
          <ol class="relative mt-3 space-y-2 border-l border-zinc-200 pl-4 dark:border-zinc-800">
            <li v-if="cardHistory.length === 0" class="text-sm text-muted-foreground">{{ t('crm.noHistory') }}</li>
            <li v-for="(h, i) in cardHistory" :key="i" class="relative">
              <span class="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full bg-sky-500 ring-4 ring-background" />
              <p class="text-[13px]">{{ historyLine(h.diff) }}</p>
              <p class="text-[11px] text-muted-foreground">{{ d(h.changed_at, 'short') }}</p>
            </li>
          </ol>
        </details>

        <div class="flex justify-end">
          <Button variant="ghost" size="sm" @click="card = null">{{ t('crm.close') }}</Button>
        </div>
      </div>
    </Dialog>

    <!-- Диалог создания -->
    <Dialog v-model:open="createOpen" :title="t('sal.newOrder')" width="640px">
      <form class="space-y-4" @submit.prevent="saveOrder">
        <div class="grid grid-cols-2 gap-3">
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('sal.colCustomer') }}</Label>
            <SearchSelect v-model="createForm.counterpartyId" :options="[
              { value: '', label: createForm.dealId ? t('sal.fromDeal') : '—' },
              ...counterparties.map((c) => ({ value: c.id, label: c.name })),
            ]" :search-placeholder="t('ui.searchPlaceholder')" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('inv.colNote') }}</Label>
            <Input v-model="createForm.note" />
          </div>
        </div>
        <p v-if="createForm.dealId" class="text-xs text-emerald-700 dark:text-emerald-400">{{ t('sal.dealPrefill') }}</p>
        <p class="text-xs font-medium text-muted-foreground">{{ t('pur.lines') }}</p>
        <div v-for="(line, index) in createForm.lines" :key="index" class="flex items-end gap-2">
          <div class="flex-1 space-y-1">
            <SearchSelect v-model="line.itemId" :options="items.map((i) => ({ value: i.id, label: `${i.sku} · ${i.name}` }))" />
          </div>
          <div class="w-24 space-y-1"><Input v-model="line.qty" :placeholder="t('inv.phQty')" inputmode="decimal" /></div>
          <div class="w-28 space-y-1"><Input v-model="line.price" :placeholder="t('inv.phPrice')" inputmode="decimal" /></div>
          <Button variant="ghost" size="icon" class="h-9 w-9" @click="createForm.lines.splice(index, 1)">✕</Button>
        </div>
        <Button variant="outline" size="sm" class="gap-1.5" @click="createForm.lines.push({ itemId: '', qty: '', price: '' })">
          <Plus class="h-3.5 w-3.5" /> {{ t('inv.addLine') }}
        </Button>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="createOpen = false">{{ t('ui.cancel') }}</Button>
          <Button variant="emerald" type="submit" size="sm" :disabled="creating">{{ t('ui.save') }}</Button>
        </div>
      </form>
    </Dialog>

    <!-- Диалог оплаты -->
    <Dialog :open="payOpen" :title="t('sal.pay')" @update:open="(v: boolean) => { if (!v) payOpen = false }">
      <form class="space-y-4" @submit.prevent="savePay">
        <p v-if="payOrder" class="text-sm text-muted-foreground">
          {{ payOrder.number }} · {{ cpName(payOrder.counterparty_id) }} · {{ formatMoney2(payOrder.amount, payOrder.currency) }}
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
          <Button variant="outline" size="sm" @click="payOpen = false">{{ t('ui.cancel') }}</Button>
          <Button variant="emerald" type="submit" size="sm">{{ t('sal.pay') }}</Button>
        </div>
      </form>
    </Dialog>

    <!-- Диалог отгрузки -->
    <Dialog v-model:open="shipOpen" :title="t('sal.ship')" width="640px">
      <form class="space-y-4" @submit.prevent="saveShipment">
        <p class="text-xs text-muted-foreground">{{ t('sal.shipHint') }}</p>
        <div v-for="(line, index) in shipForm.lines" :key="index" class="space-y-1.5 rounded-lg border border-zinc-200 p-2.5 dark:border-zinc-800">
          <div class="flex items-end gap-2">
            <span class="flex-1 truncate py-2 text-sm">{{ itemName(line.itemId) }}</span>
            <div class="w-24 space-y-1"><Input v-model="line.qty" inputmode="decimal" /></div>
            <Button variant="ghost" size="icon" class="h-9 w-9" @click="shipForm.lines.splice(index, 1)">✕</Button>
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
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="shipOpen = false">{{ t('ui.cancel') }}</Button>
          <Button variant="emerald" type="submit" size="sm" :disabled="shipSaving">{{ t('ui.save') }}</Button>
        </div>
      </form>
    </Dialog>
  </div>
</template>

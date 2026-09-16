<script setup lang="ts">
// Склад (этап I, стр. 8 макета): номенклатура с видами (товар/цифровой/
// услуга) и порогами low_stock, остатки по локациям с суммами, журнал
// движений, перемещение, инвентаризация, сборка. Кнопки проведения —
// только при accounting: rw.
// Пагинация: номенклатура/остатки/движения — порциями по 50 (infinite
// scroll); имена в payload'ах с сервера, полные справочники на клиенте
// не грузятся. Список номенклатуры для пикеров диалогов — лениво, при
// первом открытии диалога.
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ArrowLeftRight, ClipboardCheck, Cog, Package, Play, Plus,
} from 'lucide-vue-next'
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

const tab = ref('items')
const loading = ref(true)

interface Item {
  id: string; sku: string; name: string; kind: string; unit_code: string
  tracking: string; sale_price: string | null; avg_cost: string | null
  low_stock_threshold: string | null; is_active: boolean
}
interface Location { id: string; name: string; kind: string; is_transit: boolean; is_active: boolean }
interface Balance {
  item_id: string; sku: string; item_name: string; item_kind: string
  location_id: string; location_name: string; location_kind: string
  qty: string; avg_cost: string; value: string
  low_stock_threshold: string | null
}
interface Move {
  id: string; item_id: string; qty: string; unit_cost: string | null
  from_location_id: string | null; to_location_id: string | null
  counterparty_id: string | null; source_type: string; moved_at: string; note: string
  item_sku: string | null; item_name: string | null
  from_location_name: string | null; to_location_name: string | null
}
interface TechCard {
  id: string; name: string; product_item_id: string; qty_out: string
  components: Array<{ item_id: string; qty: string }>; is_active: boolean
  product_name: string | null; component_names: Record<string, string> | null
}
interface ProductionOrder {
  id: string; number: string | null; tech_card_id: string; qty_planned: string
  status: string; is_stornoed: boolean; material_cost: string; moved_at: string | null
  tech_card_name: string | null
}

const locations = ref<Location[]>([])
const techCards = ref<TechCard[]>([])
const prodOrders = ref<ProductionOrder[]>([])

// ---------- Пагинированные списки ----------
const itemsList = ref<{ reload: () => Promise<void> } | null>(null)
const balancesList = ref<{ reload: () => Promise<void> } | null>(null)
const movesList = ref<{ reload: () => Promise<void> } | null>(null)

const itemSearch = ref('')
const itemSearchKey = ref('')
let itemSearchTimer: number | undefined
watch(itemSearch, () => {
  window.clearTimeout(itemSearchTimer)
  itemSearchTimer = window.setTimeout(() => {
    itemSearchKey.value = itemSearch.value.trim()
  }, 300)
})

// поиск уходит на сервер (ILIKE + GIN pg_trgm по sku/имени, миграция 0021)
function fetchItemsPage(offset: number, limit: number) {
  const qs = itemSearchKey.value ? `&q=${encodeURIComponent(itemSearchKey.value)}` : ''
  return get<PageOf<Item>>(`/accounting/items?limit=${limit}&offset=${offset}${qs}`)
}

const balanceLocation = ref('')
const balancesKey = ref('')
watch(balanceLocation, () => { balancesKey.value = balanceLocation.value })

function fetchBalancesPage(offset: number, limit: number) {
  const qs = balanceLocation.value ? `&location_id=${balanceLocation.value}` : ''
  return get<PageOf<Balance>>(`/accounting/stock/balances?limit=${limit}&offset=${offset}${qs}`)
}

const movesLocation = ref('')
const movesKey = ref('')
watch(movesLocation, () => { movesKey.value = movesLocation.value })

function fetchMovesPage(offset: number, limit: number) {
  const qs = movesLocation.value ? `&location_id=${movesLocation.value}` : ''
  return get<PageOf<Move>>(`/accounting/stock/moves?limit=${limit}&offset=${offset}${qs}`)
}

// суммарная стоимость видимых остатков (KPI честен, только когда
// показаны все строки фильтра)
function sumValue(rows: Balance[]): number {
  return rows.reduce((sum, b) => sum + Number(b.value ?? 0), 0)
}

// ---------- 1.3: уровень остатка по порогу low_stock ----------
// порог приходит в строке остатка с сервера
type StockLevel = 'ok' | 'low' | 'crit'
function stockLevel(balance: Balance): StockLevel {
  const threshold = Number(balance.low_stock_threshold ?? 0)
  const qty = Number(balance.qty)
  if (threshold <= 0) return 'ok'
  if (qty <= 0 || qty * 2 <= threshold) return 'crit'
  if (qty <= threshold) return 'low'
  return 'ok'
}
const levelBadge = computed<Record<StockLevel, { label: string; cls: string }>>(() => ({
  ok: {
    label: t('inv.levelOk'),
    cls: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300',
  },
  low: {
    label: t('inv.levelLow'),
    cls: 'bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300',
  },
  crit: {
    label: t('inv.levelCrit'),
    cls: 'bg-red-100 text-red-700 dark:bg-red-950/60 dark:text-red-300',
  },
}))

// ---------- карточка товара (клик по строке номенклатуры) ----------
const detailItem = ref<Item | null>(null)
const detailLoading = ref(false)
const detailBalances = ref<Balance[]>([])
const detailMoves = ref<Move[]>([])

async function openItemCard(item: Item) {
  detailItem.value = item
  detailLoading.value = true
  try {
    const [balances, moves] = await Promise.all([
      get<PageOf<Balance>>(`/accounting/stock/balances?item_id=${item.id}&limit=0`),
      get<PageOf<Move>>(`/accounting/stock/moves?item_id=${item.id}&limit=10`),
    ])
    detailBalances.value = balances.items
    detailMoves.value = moves.items
  } catch (error) {
    toast.apiError(error)
  } finally {
    detailLoading.value = false
  }
}

// Д13 UI: испортить код прямо из карточки товара
const voidCode = ref('')
const voidWorking = ref(false)
async function voidSerial() {
  if (voidWorking.value || !voidCode.value.trim() || !detailItem.value) return
  voidWorking.value = true
  try {
    await post('/accounting/serials/void', { code: voidCode.value.trim() })
    toast.success(t('inv.codeVoided'))
    voidCode.value = ''
    detailItem.value = null
    await reloadStock()
  } catch (error) {
    toast.apiError(error)
  } finally {
    voidWorking.value = false
  }
}
const activeLocations = computed(() => locations.value.filter((l) => l.is_active && !l.is_transit))

// пикеры номенклатуры в диалогах: полный список грузится лениво при
// первом открытии диалога (на экране по умолчанию не нужен)
const dialogItems = ref<Item[]>([])
const dialogItemsLoading = ref(false)
async function ensureDialogItems() {
  if (dialogItems.value.length || dialogItemsLoading.value) return
  dialogItemsLoading.value = true
  try {
    dialogItems.value = (await get<PageOf<Item>>('/accounting/items?limit=0&is_active=true')).items
  } catch (error) {
    toast.apiError(error)
  } finally {
    dialogItemsLoading.value = false
  }
}
const itemOptions = computed(() =>
  dialogItems.value.map((i) => ({ value: i.id, label: `${i.sku} · ${i.name}` })))

function kindCls(kind: string): string {
  return {
    physical: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300',
    digital: 'bg-violet-100 text-violet-800 dark:bg-violet-950/60 dark:text-violet-300',
    service: 'bg-sky-100 text-sky-800 dark:bg-sky-950/60 dark:text-sky-300',
  }[kind] ?? ''
}

// лёгкие справочники экрана (локации для фильтров, сборка); тяжёлые
// списки (номенклатура/остатки/движения) живут в PaginatedList
async function loadRefData() {
  loading.value = true
  try {
    const [loc, tc, po] = await Promise.all([
      get<Location[]>('/accounting/locations'),
      get<TechCard[]>('/accounting/tech-cards'),
      get<ProductionOrder[]>('/accounting/production-orders'),
    ])
    locations.value = loc
    techCards.value = tc
    prodOrders.value = po
  } catch {
    toast.error(t('errors.unknown'))
  } finally {
    loading.value = false
  }
}
onMounted(loadRefData)

async function reloadStock() {
  await Promise.all([
    itemsList.value?.reload() ?? Promise.resolve(),
    balancesList.value?.reload() ?? Promise.resolve(),
    movesList.value?.reload() ?? Promise.resolve(),
    loadRefData(),
  ])
}

// ---------- Номенклатура ----------
const itemOpen = ref(false)
const itemSaving = ref(false)
const itemForm = reactive({ sku: '', name: '', kind: 'physical', unitCode: 'шт', tracking: 'qty', lowStock: '' })

async function saveItem() {
  if (itemSaving.value || !itemForm.sku.trim() || !itemForm.name.trim()) return
  itemSaving.value = true
  try {
    await post('/accounting/items', {
      sku: itemForm.sku.trim(), name: itemForm.name.trim(), kind: itemForm.kind,
      unit_code: itemForm.unitCode, tracking: itemForm.tracking,
      low_stock_threshold: itemForm.lowStock.trim() ? itemForm.lowStock.trim().replace(',', '.') : null,
    })
    toast.success(t('inv.itemCreated'))
    itemOpen.value = false
    dialogItems.value = [] // кэш пикеров устарел — перезагрузим при следующем открытии
    await itemsList.value?.reload()
  } catch (error) {
    toast.apiError(error)
  } finally {
    itemSaving.value = false
  }
}

// ---------- Перемещение ----------
const transferOpen = ref(false)
const transferSaving = ref(false)
const transferForm = reactive({ itemId: '', qty: '', from: '', to: '', note: '' })

function openTransfer() {
  void ensureDialogItems()
  transferOpen.value = true
}

async function saveTransfer() {
  if (transferSaving.value || !transferForm.itemId || !isPositiveDecimalString(transferForm.qty)
    || !transferForm.from || !transferForm.to) return
  transferSaving.value = true
  try {
    await post('/accounting/stock/transfer', {
      item_id: transferForm.itemId, qty: transferForm.qty.trim().replace(',', '.'),
      from_location_id: transferForm.from, to_location_id: transferForm.to,
      note: transferForm.note,
    })
    toast.success(t('inv.moved'))
    transferOpen.value = false
    await reloadStock()
  } catch (error) {
    toast.apiError(error)
  } finally {
    transferSaving.value = false
  }
}

// ---------- Инвентаризация ----------
const adjustOpen = ref(false)
const adjustSaving = ref(false)
const adjustLocation = ref('')
const adjustLines = ref<Array<{ itemId: string; qtyFact: string; codes: string }>>([])

function openAdjust() {
  void ensureDialogItems()
  adjustOpen.value = true
  adjustLines.value = []
  addAdjustLine()
}

function addAdjustLine() {
  adjustLines.value.push({ itemId: '', qtyFact: '', codes: '' })
}
function removeAdjustLine(index: number) {
  adjustLines.value.splice(index, 1)
}

async function saveAdjustment() {
  if (adjustSaving.value || !adjustLocation.value) return
  const lines = adjustLines.value
    .filter((line) => line.itemId)
    .map((line) => {
      const item = dialogItems.value.find((i) => i.id === line.itemId)
      if (item?.tracking === 'serial') {
        return {
          item_id: line.itemId,
          serial_codes: line.codes.split(/[\n,;]+/).map((c) => c.trim()).filter(Boolean),
        }
      }
      return {
        item_id: line.itemId,
        qty_fact: line.qtyFact.trim() ? line.qtyFact.trim().replace(',', '.') : null,
      }
    })
  if (!lines.length) return
  adjustSaving.value = true
  try {
    await post('/accounting/stock/adjustment', { location_id: adjustLocation.value, lines })
    toast.success(t('inv.adjusted'))
    adjustOpen.value = false
    adjustLines.value = []
    await reloadStock()
  } catch (error) {
    toast.apiError(error)
  } finally {
    adjustSaving.value = false
  }
}

// ---------- Сборка ----------
const cardOpen = ref(false)
const cardSaving = ref(false)
const cardForm = reactive({ name: '', productItemId: '', qtyOut: '', components: [] as Array<{ itemId: string; qty: string }> })

function openCard() {
  void ensureDialogItems()
  cardOpen.value = true
}

function addComponent() {
  cardForm.components.push({ itemId: '', qty: '' })
}

async function saveTechCard() {
  if (cardSaving.value || !cardForm.name.trim() || !cardForm.productItemId) return
  cardSaving.value = true
  try {
    await post('/accounting/tech-cards', {
      name: cardForm.name.trim(),
      product_item_id: cardForm.productItemId,
      qty_out: cardForm.qtyOut.trim().replace(',', '.') || '1',
      components: cardForm.components
        .filter((c) => c.itemId && isPositiveDecimalString(c.qty))
        .map((c) => ({ item_id: c.itemId, qty: c.qty.trim().replace(',', '.') })),
    })
    toast.success(t('inv.cardCreated'))
    cardOpen.value = false
    cardForm.components = []
    await loadRefData()
  } catch (error) {
    toast.apiError(error)
  } finally {
    cardSaving.value = false
  }
}

const prodOpen = ref(false)
const prodSaving = ref(false)
const prodForm = reactive({ techCardId: '', qty: '', note: '' })

async function saveProduction() {
  if (prodSaving.value || !prodForm.techCardId || !isPositiveDecimalString(prodForm.qty)) return
  prodSaving.value = true
  try {
    await post('/accounting/production-orders', {
      tech_card_id: prodForm.techCardId,
      qty_planned: prodForm.qty.trim().replace(',', '.'),
      note: prodForm.note,
    })
    toast.success(t('inv.productionCreated'))
    prodOpen.value = false
    await reloadStock()
  } catch (error) {
    toast.apiError(error)
  } finally {
    prodSaving.value = false
  }
}

async function postProduction(order: ProductionOrder) {
  try {
    await post(`/accounting/production-orders/${order.id}/post`)
    toast.success(t('inv.productionPosted'))
    await reloadStock()
  } catch (error) {
    toast.apiError(error)
  }
}

function statusCls(status: string): string {
  return {
    draft: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
    posted: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300',
    confirmed: 'bg-sky-100 text-sky-800 dark:bg-sky-950/60 dark:text-sky-300',
    received: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300',
    shipped: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300',
    cancelled: 'bg-red-100 text-red-700 dark:bg-red-950/60 dark:text-red-300',
  }[status] ?? 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400'
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div>
        <h2 class="text-lg font-bold tracking-tight">{{ t('inv.title') }}</h2>
        <p class="text-sm text-muted-foreground">{{ t('inv.subtitle') }}</p>
      </div>
    </div>

    <Tabs
      :tabs="[
        { key: 'items', label: t('inv.tabItems') },
        { key: 'balances', label: t('inv.tabBalances') },
        { key: 'moves', label: t('inv.tabMoves') },
        { key: 'production', label: t('inv.tabProduction') },
      ]"
      :model-value="tab"
      @update:model-value="tab = $event"
    />

    <!-- Номенклатура -->
    <div v-if="tab === 'items'" class="space-y-3">
      <PaginatedList
        ref="itemsList" :fetch-page="fetchItemsPage" :reset-key="`i:${itemSearchKey}`"
        v-slot="{ items: itemRows, loading }"
      >
        <div class="flex flex-wrap items-center gap-2">
          <Input v-model="itemSearch" :placeholder="t('inv.searchItems')" class="max-w-xs" />
          <Button v-if="canWrite" variant="emerald" size="sm" class="ml-auto gap-1.5" @click="itemOpen = true">
            <Plus class="h-3.5 w-3.5" /> {{ t('inv.newItem') }}
          </Button>
        </div>
        <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
          <CardContent class="p-0">
            <div v-if="loading" class="space-y-2 p-4"><Skeleton class="h-10 w-full" /><Skeleton class="h-10 w-full" /></div>
            <div v-else-if="itemRows.length === 0" class="p-6"><EmptyState :title="t('ui.emptyTitle')" :description="t('ui.emptyDescription')" /></div>
            <div v-else class="overflow-x-auto">
              <table class="w-full text-sm">
                <thead>
                  <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                    <th class="px-3 py-2 font-medium">{{ t('inv.colSku') }}</th>
                    <th class="px-3 py-2 font-medium">{{ t('inv.colName') }}</th>
                    <th class="px-3 py-2 font-medium">{{ t('inv.colKind') }}</th>
                    <th class="hidden px-3 py-2 font-medium sm:table-cell">{{ t('inv.colUnit') }}</th>
                    <th class="hidden px-3 py-2 font-medium md:table-cell">{{ t('inv.colTracking') }}</th>
                    <th class="px-3 py-2 text-right font-medium">{{ t('inv.colAvgCost') }}</th>
                    <th class="hidden px-3 py-2 text-right font-medium lg:table-cell">{{ t('inv.colLowStock') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="item in itemRows" :key="item.id"
                    class="cursor-pointer border-t border-zinc-100 transition-colors hover:bg-zinc-50/60 dark:border-zinc-800/70 dark:hover:bg-zinc-800/40"
                    @click="openItemCard(item)"
                  >
                    <td class="px-3 py-2 font-mono text-xs text-emerald-700 dark:text-emerald-400">{{ item.sku }}</td>
                    <td class="px-3 py-2 font-medium">{{ item.name }}</td>
                    <td class="px-3 py-2"><Badge :class="kindCls(item.kind)">{{ t(`inv.kind.${item.kind}`) }}</Badge></td>
                    <td class="hidden px-3 py-2 text-muted-foreground sm:table-cell">{{ item.unit_code }}</td>
                    <td class="hidden px-3 py-2 text-xs text-muted-foreground md:table-cell">{{ item.tracking === 'serial' ? t('inv.serial') : '—' }}</td>
                    <td class="whitespace-nowrap px-3 py-2 text-right text-muted-foreground">{{ formatMoney2(item.avg_cost) }}</td>
                    <td class="hidden px-3 py-2 text-right lg:table-cell">
                      <Badge v-if="item.low_stock_threshold" class="bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300">
                        ≥ {{ item.low_stock_threshold }}
                      </Badge>
                      <span v-else class="text-muted-foreground">—</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </PaginatedList>
    </div>

    <!-- Остатки -->
    <div v-else-if="tab === 'balances'" class="space-y-3">
      <PaginatedList
        ref="balancesList" :fetch-page="fetchBalancesPage" :reset-key="`b:${balancesKey}`"
        v-slot="{ items: balanceRows, loading, total }"
      >
        <div class="flex flex-wrap items-center gap-2">
          <div class="w-[240px]">
            <Select v-model="balanceLocation" :options="[
              { value: '', label: t('inv.allLocations') },
              ...locations.map((l) => ({ value: l.id, label: l.name })),
            ]" />
          </div>
          <span v-if="balanceRows.length === total" class="text-sm text-muted-foreground">
            {{ t('inv.totalValue') }}: <span class="font-semibold text-foreground">{{ formatMoney2(sumValue(balanceRows).toFixed(2)) }}</span>
          </span>
          <div v-if="canWrite" class="ml-auto flex gap-2">
            <Button variant="outline" size="sm" class="gap-1.5" @click="openTransfer">
              <ArrowLeftRight class="h-3.5 w-3.5" /> {{ t('inv.transfer') }}
            </Button>
            <Button variant="outline" size="sm" class="gap-1.5" @click="openAdjust">
              <ClipboardCheck class="h-3.5 w-3.5" /> {{ t('inv.adjustment') }}
            </Button>
          </div>
        </div>
        <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
          <CardContent class="p-0">
            <div v-if="loading" class="space-y-2 p-4"><Skeleton class="h-10 w-full" /></div>
            <div v-else-if="balanceRows.length === 0" class="p-6"><EmptyState :title="t('ui.emptyTitle')" :description="t('ui.emptyDescription')" /></div>
            <div v-else class="overflow-x-auto">
              <table class="w-full text-sm">
                <thead>
                  <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                    <th class="px-3 py-2 font-medium">{{ t('inv.colSku') }}</th>
                    <th class="px-3 py-2 font-medium">{{ t('inv.colName') }}</th>
                    <th class="px-3 py-2 font-medium">{{ t('inv.colLocation') }}</th>
                    <th class="px-3 py-2 text-right font-medium">{{ t('inv.colQty') }}</th>
                    <th class="hidden px-3 py-2 text-right font-medium sm:table-cell">{{ t('inv.colAvgCost') }}</th>
                    <th class="px-3 py-2 text-right font-medium">{{ t('inv.colValue') }}</th>
                    <th class="px-3 py-2 font-medium">{{ t('inv.colLevel') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="b in balanceRows" :key="`${b.item_id}-${b.location_id}`" class="border-t border-zinc-100 dark:border-zinc-800/70">
                    <td class="px-3 py-2 font-mono text-xs">{{ b.sku }}</td>
                    <td class="px-3 py-2 font-medium">
                      {{ b.item_name }}
                      <Badge :class="['ml-1.5', kindCls(b.item_kind)]">{{ t(`inv.kind.${b.item_kind}`) }}</Badge>
                    </td>
                    <td class="px-3 py-2 text-muted-foreground">{{ b.location_name }}</td>
                    <td class="px-3 py-2 text-right font-semibold tabular-nums">{{ Number(b.qty).toLocaleString('ru-RU') }}</td>
                    <td class="hidden px-3 py-2 text-right text-muted-foreground sm:table-cell">{{ formatMoney2(b.avg_cost) }}</td>
                    <td class="whitespace-nowrap px-3 py-2 text-right tabular-nums">{{ formatMoney2(b.value) }}</td>
                    <td class="px-3 py-2"><Badge :class="levelBadge[stockLevel(b)].cls">{{ levelBadge[stockLevel(b)].label }}</Badge></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </PaginatedList>
    </div>

    <!-- Движения -->
    <div v-else-if="tab === 'moves'" class="space-y-3">
      <PaginatedList
        ref="movesList" :fetch-page="fetchMovesPage" :reset-key="`m:${movesKey}`"
        v-slot="{ items: moveRows, loading }"
      >
        <div class="w-[240px]">
          <Select v-model="movesLocation" :options="[
            { value: '', label: t('inv.allLocations') },
            ...locations.map((l) => ({ value: l.id, label: l.name })),
          ]" />
        </div>
        <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
          <CardContent class="p-0">
            <div v-if="loading" class="space-y-2 p-4"><Skeleton class="h-10 w-full" /></div>
            <div v-else-if="moveRows.length === 0" class="p-6"><EmptyState :title="t('ui.emptyTitle')" :description="t('ui.emptyDescription')" /></div>
            <div v-else class="overflow-x-auto">
              <table class="w-full text-sm">
                <thead>
                  <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                    <th class="px-3 py-2 font-medium">{{ t('inv.colDate') }}</th>
                    <th class="px-3 py-2 font-medium">{{ t('inv.colName') }}</th>
                    <th class="px-3 py-2 font-medium">{{ t('inv.colQty') }}</th>
                    <th class="hidden px-3 py-2 font-medium md:table-cell">{{ t('inv.colRoute') }}</th>
                    <th class="px-3 py-2 font-medium">{{ t('inv.colSource') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="mv in moveRows" :key="mv.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
                    <td class="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">{{ mv.moved_at }}</td>
                    <td class="px-3 py-2">
                      {{ mv.item_name ?? '…' }}
                      <span class="ml-1 font-mono text-xs text-muted-foreground">{{ mv.item_sku }}</span>
                    </td>
                    <td class="px-3 py-2 font-semibold tabular-nums">{{ Number(mv.qty).toLocaleString('ru-RU') }}</td>
                    <td class="hidden px-3 py-2 text-xs text-muted-foreground md:table-cell">
                      {{ mv.from_location_name ?? '—' }} → {{ mv.to_location_name ?? '—' }}
                    </td>
                    <td class="px-3 py-2"><Badge variant="outline">{{ mv.source_type }}</Badge></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </PaginatedList>
    </div>

    <!-- Сборка -->
    <div v-else class="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="p-0">
          <div class="flex items-center justify-between px-4 py-3">
            <p class="flex items-center gap-2 text-sm font-semibold"><Cog class="h-4 w-4 text-emerald-600" /> {{ t('inv.techCards') }}</p>
            <Button v-if="canWrite" variant="outline" size="sm" class="gap-1.5" @click="openCard">
              <Plus class="h-3.5 w-3.5" /> {{ t('inv.newCard') }}
            </Button>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <tbody>
                <tr v-for="card in techCards.slice(0, 50)" :key="card.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
                  <td class="px-3 py-2">
                    <p class="font-medium">{{ card.name }}</p>
                    <p class="text-xs text-muted-foreground">
                      {{ card.product_name ?? '…' }} × {{ Number(card.qty_out).toLocaleString('ru-RU') }}
                      ← {{ card.components.map((c) => `${card.component_names?.[c.item_id] ?? '…'}×${Number(c.qty).toLocaleString('ru-RU')}`).join(', ') }}
                    </p>
                  </td>
                  <td class="px-3 py-2 text-right">
                    <Button v-if="canWrite" variant="ghost" size="icon" class="h-7 w-7" :title="t('inv.newProduction')" @click="prodForm.techCardId = card.id; prodForm.qty = card.qty_out; prodOpen = true">
                      <Play class="h-3.5 w-3.5 text-emerald-600" />
                    </Button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="p-0">
          <div class="flex items-center justify-between px-4 py-3">
            <p class="flex items-center gap-2 text-sm font-semibold"><Package class="h-4 w-4 text-emerald-600" /> {{ t('inv.productionOrders') }}</p>
            <Button v-if="canWrite" variant="outline" size="sm" class="gap-1.5" @click="prodOpen = true">
              <Plus class="h-3.5 w-3.5" /> {{ t('inv.newProduction') }}
            </Button>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                  <th class="px-3 py-2 font-medium">№</th>
                  <th class="px-3 py-2 font-medium">{{ t('inv.colCard') }}</th>
                  <th class="px-3 py-2 text-right font-medium">{{ t('inv.colQty') }}</th>
                  <th class="px-3 py-2 text-right font-medium">{{ t('inv.colCost') }}</th>
                  <th class="px-3 py-2 font-medium">{{ t('inv.colStatus') }}</th>
                  <th v-if="canWrite" class="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                <tr v-for="order in prodOrders.slice(0, 50).reverse()" :key="order.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
                  <td class="px-3 py-2 font-medium text-emerald-700 dark:text-emerald-400">{{ order.number ?? '—' }}</td>
                  <td class="px-3 py-2 text-xs">{{ order.tech_card_name ?? techCards.find((c) => c.id === order.tech_card_id)?.name ?? '…' }}</td>
                  <td class="px-3 py-2 text-right tabular-nums">{{ Number(order.qty_planned).toLocaleString('ru-RU') }}</td>
                  <td class="whitespace-nowrap px-3 py-2 text-right tabular-nums">{{ formatMoney2(order.material_cost) }}</td>
                  <td class="px-3 py-2"><Badge :class="statusCls(order.status)">{{ t(`inv.status.${order.status}`) }}</Badge></td>
                  <td v-if="canWrite" class="px-3 py-2 text-right">
                    <Button v-if="order.status === 'draft'" variant="outline" size="sm" @click="postProduction(order)">
                      {{ t('inv.post') }}
                    </Button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>

    <!-- Карточка товара (1.3): остатки по локациям, движения, порча кода -->
    <Dialog
      :open="detailItem !== null" :title="detailItem?.name ?? ''" width="640px"
      @update:open="(v: boolean) => { if (!v) detailItem = null }"
    >
      <div v-if="detailItem" class="space-y-4">
        <div class="flex flex-wrap items-center gap-2">
          <Badge :class="kindCls(detailItem.kind)">{{ t(`inv.kind.${detailItem.kind}`) }}</Badge>
          <Badge variant="outline" class="font-mono text-[11px]">{{ detailItem.sku }}</Badge>
          <span v-if="detailItem.tracking === 'serial'" class="text-xs text-muted-foreground">{{ t('inv.serial') }}</span>
          <span class="ml-auto text-sm font-semibold">{{ formatMoney2(detailItem.avg_cost) }}</span>
        </div>

        <div v-if="detailLoading" class="space-y-2">
          <Skeleton class="h-8 w-full" />
          <Skeleton class="h-8 w-full" />
        </div>
        <template v-else>
          <div>
            <p class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{{ t('inv.tabBalances') }}</p>
            <table class="mt-1.5 w-full text-sm">
              <tbody>
                <tr v-for="b in detailBalances" :key="b.location_id" class="border-t border-zinc-100 dark:border-zinc-800/70">
                  <td class="py-1.5">{{ b.location_name }}</td>
                  <td class="py-1.5 text-right font-semibold tabular-nums">{{ Number(b.qty).toLocaleString('ru-RU') }}</td>
                  <td class="py-1.5 text-right"><Badge :class="levelBadge[stockLevel(b)].cls">{{ levelBadge[stockLevel(b)].label }}</Badge></td>
                </tr>
                <tr v-if="detailBalances.length === 0">
                  <td colspan="3" class="py-3 text-center text-muted-foreground">{{ t('ui.emptyDescription') }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div v-if="detailMoves.length">
            <p class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{{ t('inv.tabMoves') }}</p>
            <ul class="mt-1.5 space-y-1 text-xs text-muted-foreground">
              <li v-for="mv in detailMoves" :key="mv.id" class="flex justify-between gap-2">
                <span class="truncate">{{ mv.moved_at }} · {{ mv.from_location_name ?? '—' }} → {{ mv.to_location_name ?? '—' }}</span>
                <span class="shrink-0 font-semibold">{{ Number(mv.qty).toLocaleString('ru-RU') }}</span>
              </li>
            </ul>
          </div>
        </template>

        <!-- Д13: испортить код (цифровые серийные) -->
        <form
          v-if="canWrite && detailItem.tracking === 'serial'"
          class="flex items-end gap-2 rounded-xl border border-red-200 bg-red-50/50 p-3 dark:border-red-900/60 dark:bg-red-950/20"
          @submit.prevent="voidSerial"
        >
          <div class="flex-1 space-y-1">
            <Label class="text-xs font-medium text-red-700 dark:text-red-400">{{ t('inv.voidCode') }}</Label>
            <Input v-model="voidCode" placeholder="LICENSE-…" />
            <p class="text-[11px] text-muted-foreground">{{ t('inv.voidHint') }}</p>
          </div>
          <Button variant="destructive" type="submit" size="sm" :disabled="voidWorking || voidCode.trim().length < 3">
            {{ t('inv.voidAction') }}
          </Button>
        </form>
      </div>
    </Dialog>

    <!-- Диалог: номенклатура -->
    <Dialog v-model:open="itemOpen" :title="t('inv.newItem')" width="560px">
      <form class="space-y-4" @submit.prevent="saveItem">
        <div class="grid grid-cols-2 gap-3">
          <div class="space-y-1.5"><Label class="text-xs font-medium">{{ t('inv.colSku') }}</Label><Input v-model="itemForm.sku" :placeholder="t('inv.phSku')" /></div>
          <div class="space-y-1.5"><Label class="text-xs font-medium">{{ t('inv.colName') }}</Label><Input v-model="itemForm.name" /></div>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('inv.colKind') }}</Label>
            <Select v-model="itemForm.kind" :options="[
              { value: 'physical', label: t('inv.kind.physical') },
              { value: 'digital', label: t('inv.kind.digital') },
              { value: 'service', label: t('inv.kind.service') },
            ]" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('inv.colTracking') }}</Label>
            <Select v-model="itemForm.tracking" :options="[
              { value: 'qty', label: '—' },
              { value: 'serial', label: t('inv.serial') },
            ]" />
          </div>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div class="space-y-1.5"><Label class="text-xs font-medium">{{ t('inv.colUnit') }}</Label><Input v-model="itemForm.unitCode" /></div>
          <div class="space-y-1.5"><Label class="text-xs font-medium">{{ t('inv.colLowStock') }}</Label><Input v-model="itemForm.lowStock" placeholder="10" inputmode="decimal" /></div>
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="itemOpen = false">{{ t('ui.cancel') }}</Button>
          <Button variant="emerald" type="submit" size="sm" :disabled="itemSaving || !itemForm.sku.trim() || !itemForm.name.trim()">{{ t('ui.save') }}</Button>
        </div>
      </form>
    </Dialog>

    <!-- Диалог: перемещение -->
    <Dialog v-model:open="transferOpen" :title="t('inv.transfer')" width="520px">
      <form class="space-y-4" @submit.prevent="saveTransfer">
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('inv.colItem') }}</Label>
          <SearchSelect v-model="transferForm.itemId" :options="itemOptions" :search-placeholder="t('ui.searchPlaceholder')" />
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('inv.fromLocation') }}</Label>
            <SearchSelect v-model="transferForm.from" :options="activeLocations.map((l) => ({ value: l.id, label: l.name }))" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('inv.toLocation') }}</Label>
            <SearchSelect v-model="transferForm.to" :options="activeLocations.map((l) => ({ value: l.id, label: l.name }))" />
          </div>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div class="space-y-1.5"><Label class="text-xs font-medium">{{ t('inv.colQty') }}</Label><Input v-model="transferForm.qty" inputmode="decimal" /></div>
          <div class="space-y-1.5"><Label class="text-xs font-medium">{{ t('inv.colNote') }}</Label><Input v-model="transferForm.note" /></div>
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="transferOpen = false">{{ t('ui.cancel') }}</Button>
          <Button variant="emerald" type="submit" size="sm" :disabled="transferSaving">{{ t('inv.transfer') }}</Button>
        </div>
      </form>
    </Dialog>

    <!-- Диалог: инвентаризация -->
    <Dialog v-model:open="adjustOpen" :title="t('inv.adjustment')" width="620px">
      <form class="space-y-4" @submit.prevent="saveAdjustment">
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('inv.colLocation') }}</Label>
          <SearchSelect v-model="adjustLocation" :options="activeLocations.map((l) => ({ value: l.id, label: l.name }))" />
        </div>
        <p class="text-xs text-muted-foreground">{{ t('inv.adjustmentHint') }}</p>
        <div v-for="(line, index) in adjustLines" :key="index" class="flex items-end gap-2">
          <div class="flex-1 space-y-1">
            <SearchSelect v-model="line.itemId" :options="itemOptions" />
          </div>
          <div v-if="dialogItems.find((i) => i.id === line.itemId)?.tracking === 'serial'" class="flex-1 space-y-1">
            <textarea
              v-model="line.codes" rows="1" :placeholder="t('inv.codesPlaceholder')"
              class="w-full rounded-lg border border-input bg-background px-3 py-2 text-sm shadow-sm"
            />
          </div>
          <div v-else class="w-28 space-y-1">
            <Input v-model="line.qtyFact" :placeholder="t('inv.phFact')" inputmode="decimal" />
          </div>
          <Button variant="ghost" size="icon" class="h-9 w-9" @click="removeAdjustLine(index)">✕</Button>
        </div>
        <Button variant="outline" size="sm" class="gap-1.5" @click="addAdjustLine">
          <Plus class="h-3.5 w-3.5" /> {{ t('inv.addLine') }}
        </Button>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="adjustOpen = false">{{ t('ui.cancel') }}</Button>
          <Button variant="emerald" type="submit" size="sm" :disabled="adjustSaving || !adjustLocation">{{ t('ui.save') }}</Button>
        </div>
      </form>
    </Dialog>

    <!-- Диалог: тех.карта -->
    <Dialog v-model:open="cardOpen" :title="t('inv.newCard')" width="560px">
      <form class="space-y-4" @submit.prevent="saveTechCard">
        <div class="grid grid-cols-2 gap-3">
          <div class="space-y-1.5"><Label class="text-xs font-medium">{{ t('inv.colName') }}</Label><Input v-model="cardForm.name" /></div>
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('inv.colProduct') }}</Label>
            <SearchSelect v-model="cardForm.productItemId" :options="itemOptions" />
          </div>
        </div>
        <div class="w-32 space-y-1.5"><Label class="text-xs font-medium">{{ t('inv.qtyOut') }}</Label><Input v-model="cardForm.qtyOut" inputmode="decimal" /></div>
        <p class="text-xs font-medium text-muted-foreground">{{ t('inv.components') }}</p>
        <div v-for="(component, index) in cardForm.components" :key="index" class="flex items-end gap-2">
          <div class="flex-1 space-y-1">
            <SearchSelect v-model="component.itemId" :options="itemOptions" />
          </div>
          <div class="w-28 space-y-1"><Input v-model="component.qty" inputmode="decimal" /></div>
          <Button variant="ghost" size="icon" class="h-9 w-9" @click="cardForm.components.splice(index, 1)">✕</Button>
        </div>
        <Button variant="outline" size="sm" class="gap-1.5" @click="addComponent"><Plus class="h-3.5 w-3.5" /> {{ t('inv.addComponent') }}</Button>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="cardOpen = false">{{ t('ui.cancel') }}</Button>
          <Button variant="emerald" type="submit" size="sm" :disabled="cardSaving">{{ t('ui.save') }}</Button>
        </div>
      </form>
    </Dialog>

    <!-- Диалог: заказ на сборку -->
    <Dialog v-model:open="prodOpen" :title="t('inv.newProduction')" width="520px">
      <form class="space-y-4" @submit.prevent="saveProduction">
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('inv.colCard') }}</Label>
          <Select v-model="prodForm.techCardId" :options="techCards.map((c) => ({ value: c.id, label: c.name }))" />
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div class="space-y-1.5"><Label class="text-xs font-medium">{{ t('inv.colQty') }}</Label><Input v-model="prodForm.qty" inputmode="decimal" /></div>
          <div class="space-y-1.5"><Label class="text-xs font-medium">{{ t('inv.colNote') }}</Label><Input v-model="prodForm.note" /></div>
        </div>
        <p class="text-xs text-muted-foreground">{{ t('inv.productionHint') }}</p>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="prodOpen = false">{{ t('ui.cancel') }}</Button>
          <Button variant="emerald" type="submit" size="sm" :disabled="prodSaving">{{ t('ui.save') }}</Button>
        </div>
      </form>
    </Dialog>
  </div>
</template>

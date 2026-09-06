<script setup lang="ts">
// Продажи — CRM (этап E редизайна, стр. 4–6 макета): список сделок с
// инлайн-сменой стадии по move-правилам (won/lost только из открытой,
// выход из won/lost — только в открытую, won↔lost напрямую запрещён),
// карточка с таймлайном коммуникаций, задачами и историей record_versions,
// печать карточки — механика data-erp-print. Кнопка «Создать заказ» из
// выигранной сделки — заготовка: полный диалог появится в этапе I экранов
// ресурсов (POST /accounting/sales/orders c crm_deal_id).
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  CheckCircle2, ChevronDown, Circle, ListChecks, MessageSquare, Plus,
  Printer, ShoppingCart, Trophy,
} from 'lucide-vue-next'
import { api, get, post } from '../api/client'
import {
  Badge, Button, Card, CardContent, Dialog, DropdownMenu, DropdownMenuItem,
  EmptyState, Input, Label, Select, Skeleton, useToast,
} from '../components/ui'
import QuasarMark from '../components/brand/QuasarMark.vue'
import PrintArea from '../components/ui/PrintArea.vue'
import { useAuthStore } from '../stores/auth'
import { formatMoney2, isPositiveDecimalString } from '../utils/money'

const { t, d } = useI18n()
const auth = useAuthStore()
const toast = useToast()

const canWrite = computed(() => auth.moduleLevel('crm') === 'rw')

interface Stage {
  id: string
  name: string
  position: number
  probability: number | null
  is_won: boolean
  is_lost: boolean
}
interface Deal {
  id: string
  title: string
  stage_id: string
  counterparty_id: string | null
  counterparty_name: string | null
  contact_id: string | null
  responsible_id: string | null
  responsible_name: string | null
  amount: string
  currency: string
  rate: string | null
  amount_base: string | null
  expected_close_at: string | null
  lost_reason: string | null
  created_at: string
}
interface Communication {
  id: string
  kind: string
  content: string
  occurred_at: string | null
  created_at: string
}
interface Activity {
  id: string
  title: string
  due_at: string | null
  done: boolean
}
interface HistoryRow {
  changed_by: string | null
  changed_at: string
  diff: Record<string, { old: unknown; new: unknown }>
  reason: string | null
}

const loading = ref(true)
const stages = ref<Stage[]>([])
const deals = ref<Deal[]>([])
const query = ref('')
const stageFilter = ref('')

const openStages = computed(() => stages.value.filter((s) => !s.is_won && !s.is_lost))
const wonStage = computed(() => stages.value.find((s) => s.is_won))

function stageOf(id: string): Stage | undefined {
  return stages.value.find((s) => s.id === id)
}

// move-правила (§ mini_crm.service): фронт дублирует для disabled-пунктов,
// сервер остаётся источником истины
function moveAllowed(from: Stage | undefined, to: Stage): boolean {
  if (!from || from.id === to.id) return false
  if ((from.is_won || from.is_lost) && (to.is_won || to.is_lost)) return false
  return true
}

async function loadStages() {
  stages.value = await get<Stage[]>('/crm/stages')
}

async function loadDeals() {
  const params = new URLSearchParams()
  if (query.value.trim()) params.set('q', query.value.trim())
  if (stageFilter.value) params.set('stage_id', stageFilter.value)
  const suffix = params.size ? `?${params.toString()}` : ''
  deals.value = await get<Deal[]>(`/crm/deals${suffix}`)
}

let searchTimer: number | undefined
watch(query, () => {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => { void loadDeals().catch(() => {}) }, 300)
})

async function loadAll() {
  loading.value = true
  try {
    await Promise.all([loadStages(), loadDeals()])
  } catch {
    toast.error(t('errors.unknown'))
  } finally {
    loading.value = false
  }
}
onMounted(loadAll)

const totalBase = computed(() =>
  deals.value.reduce((sum, deal) => sum + Number(deal.amount_base ?? deal.amount), 0))

function stageClass(stage: Stage | undefined): string {
  if (!stage) return ''
  if (stage.is_won) return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300'
  if (stage.is_lost) return 'bg-red-100 text-red-700 dark:bg-red-950/60 dark:text-red-300'
  const open = ['bg-sky-100 text-sky-800 dark:bg-sky-950/60 dark:text-sky-300',
    'bg-teal-100 text-teal-800 dark:bg-teal-950/60 dark:text-teal-300',
    'bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300',
    'bg-violet-100 text-violet-800 dark:bg-violet-950/60 dark:text-violet-300']
  const idx = openStages.value.findIndex((s) => s.id === stage.id)
  return open[idx % open.length]
}

async function moveDeal(deal: Deal, to: Stage) {
  if (!moveAllowed(stageOf(deal.stage_id), to)) return
  try {
    await post<Deal>(`/crm/deals/${deal.id}/move`, { stage_id: to.id })
    toast.success(t('crm.moved', { stage: to.name }))
    await loadDeals()
    if (card.value?.id === deal.id) await openCard(deal.id)
  } catch (error) {
    toast.apiError(error)
    await loadDeals()
  }
}

// ---------- Новая сделка ----------

const createOpen = ref(false)
const creating = ref(false)
const counterparties = ref<Array<{ id: string; name: string }>>([])
const createForm = reactive({
  title: '', stageId: '', amount: '', currency: 'RUB',
  counterpartyId: '', expectedCloseAt: '',
})

async function openCreate() {
  createForm.title = ''
  createForm.stageId = openStages.value[0]?.id ?? ''
  createForm.amount = ''
  createForm.currency = 'RUB'
  createForm.counterpartyId = ''
  createForm.expectedCloseAt = ''
  createOpen.value = true
  try {
    counterparties.value = await get<Array<{ id: string; name: string }>>('/accounting/counterparties')
  } catch {
    counterparties.value = [] // без доступа к учёту — сделка без контрагента
  }
}

async function saveDeal() {
  if (creating.value || !createForm.title.trim() || !isPositiveDecimalString(createForm.amount)) {
    return
  }
  creating.value = true
  try {
    await post('/crm/deals', {
      title: createForm.title.trim(),
      stage_id: createForm.stageId,
      amount: createForm.amount.trim().replace(',', '.'),
      currency: createForm.currency,
      counterparty_id: createForm.counterpartyId || null,
      expected_close_at: createForm.expectedCloseAt || null,
    })
    toast.success(t('crm.created'))
    createOpen.value = false
    await loadDeals()
  } catch (error) {
    toast.apiError(error)
  } finally {
    creating.value = false
  }
}

// ---------- Карточка ----------

const card = ref<Deal | null>(null)
const communications = ref<Communication[]>([])
const activities = ref<Activity[]>([])
const history = ref<HistoryRow[]>([])
const commForm = reactive({ kind: 'note', content: '' })
const taskForm = reactive({ title: '', dueAt: '' })

async function openCard(id: string) {
  const [deal, comms, acts, hist] = await Promise.all([
    get<Deal>(`/crm/deals/${id}`),
    get<Communication[]>(`/crm/deals/${id}/communications`),
    get<Activity[]>(`/crm/deals/${id}/activities`),
    get<HistoryRow[]>(`/crm/history/crm.deal/${id}`),
  ])
  card.value = deal
  communications.value = comms
  activities.value = acts
  history.value = hist
}

function closeCard() {
  card.value = null
}

async function addCommunication() {
  if (!card.value || !commForm.content.trim()) return
  try {
    await post(`/crm/deals/${card.value.id}/communications`, {
      kind: commForm.kind, content: commForm.content.trim(),
    })
    commForm.content = ''
    communications.value = await get<Communication[]>(`/crm/deals/${card.value.id}/communications`)
  } catch (error) {
    toast.apiError(error)
  }
}

async function addTask() {
  if (!card.value || !taskForm.title.trim() || !taskForm.dueAt) return
  try {
    await post(`/crm/deals/${card.value.id}/activities`, {
      title: taskForm.title.trim(), due_at: taskForm.dueAt,
    })
    taskForm.title = ''
    taskForm.dueAt = ''
    activities.value = await get<Activity[]>(`/crm/deals/${card.value.id}/activities`)
  } catch (error) {
    toast.apiError(error)
  }
}

async function toggleTask(activity: Activity) {
  if (!card.value) return
  try {
    await api(`/crm/activities/${activity.id}`, { method: 'PATCH', body: JSON.stringify({ done: !activity.done }) })
    activities.value = await get<Activity[]>(`/crm/deals/${card.value.id}/activities`)
  } catch (error) {
    toast.apiError(error)
  }
}

function historyLine(diff: Record<string, { old: unknown; new: unknown }>): string {
  return Object.entries(diff)
    .map(([field, change]) => `${field}: ${String(change.old ?? '—')} → ${String(change.new ?? '—')}`)
    .join('; ')
}

// «Создать заказ» из выигранной сделки — заготовка (этап I): показываем
// эндпоинт, полный диалог появится вместе с экраном продаж ресурсов
function orderStub() {
  toast.success(t('crm.orderStubTitle'), t('crm.orderStubDesc'))
}

// ---------- Печать ----------

const printOpen = ref(false)

function doPrint() {
  window.print()
}

const commKinds: Record<string, string> = {
  note: 'crm.kindNote', call: 'crm.kindCall', email: 'crm.kindEmail', meeting: 'crm.kindMeeting',
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div>
        <h2 class="text-lg font-bold tracking-tight">{{ t('crm.title') }}</h2>
        <p class="text-sm text-muted-foreground">{{ t('crm.subtitle') }}</p>
      </div>
      <Button v-if="canWrite" variant="emerald" size="sm" class="gap-1.5" @click="openCreate">
        <Plus class="h-4 w-4" /> {{ t('crm.newDeal') }}
      </Button>
    </div>

    <!-- Фильтры -->
    <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
        <Input v-model="query" :placeholder="t('crm.searchPlaceholder')" class="flex-1" />
        <div class="w-full sm:w-[220px]">
          <Select
            v-model="stageFilter" :options="[
              { value: '', label: t('crm.allStages') },
              ...stages.map((s) => ({ value: s.id, label: s.name })),
            ]" @update:model-value="void loadDeals()"
          />
        </div>
      </CardContent>
    </Card>

    <p class="text-sm text-muted-foreground">
      {{ t('crm.found', { n: deals.length }) }}
      <span class="font-semibold text-foreground">{{ formatMoney2(totalBase.toFixed(2)) }}</span>
    </p>

    <!-- Таблица сделок -->
    <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-0">
        <div v-if="loading" class="space-y-2 p-4">
          <Skeleton class="h-10 w-full" />
          <Skeleton class="h-10 w-full" />
          <Skeleton class="h-10 w-full" />
        </div>
        <div v-else-if="deals.length === 0" class="p-6">
          <EmptyState :title="t('ui.emptyTitle')" :description="t('ui.emptyDescription')" />
        </div>
        <div v-else class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                <th class="px-3 py-2 font-medium">{{ t('crm.colDeal') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('crm.colCounterparty') }}</th>
                <th class="px-3 py-2 text-right font-medium">{{ t('crm.colAmount') }}</th>
                <th class="hidden px-3 py-2 text-center font-medium md:table-cell">{{ t('crm.colProbability') }}</th>
                <th class="hidden px-3 py-2 font-medium lg:table-cell">{{ t('crm.colResponsible') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('crm.colStage') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="deal in deals" :key="deal.id"
                class="cursor-pointer border-t border-zinc-100 transition-colors hover:bg-zinc-50/60 dark:border-zinc-800/70 dark:hover:bg-zinc-800/40"
                @click="void openCard(deal.id)"
              >
                <td class="px-3 py-2">
                  <p class="font-medium group-hover:underline">{{ deal.title }}</p>
                  <p class="text-[11px] text-muted-foreground">{{ d(deal.created_at, 'short') }}</p>
                </td>
                <td class="px-3 py-2">{{ deal.counterparty_name ?? '—' }}</td>
                <td class="whitespace-nowrap px-3 py-2 text-right font-semibold">
                  {{ formatMoney2(deal.amount, deal.currency) }}
                  <span v-if="deal.amount_base && deal.currency !== 'RUB'" class="block text-[10px] font-normal text-muted-foreground">
                    {{ formatMoney2(deal.amount_base) }}
                  </span>
                </td>
                <td class="hidden px-3 py-2 text-center text-muted-foreground md:table-cell">
                  {{ stageOf(deal.stage_id)?.probability ?? '—' }}%
                </td>
                <td class="hidden px-3 py-2 text-muted-foreground lg:table-cell">
                  {{ deal.responsible_name ?? '—' }}
                </td>
                <td class="px-3 py-2" @click.stop>
                  <DropdownMenu v-if="canWrite" align="start">
                    <template #trigger>
                      <span
                        :title="t('crm.changeStage')"
                        class="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold transition-transform hover:scale-[1.04] active:scale-95"
                        :class="stageClass(stageOf(deal.stage_id))"
                      >
                        {{ stageOf(deal.stage_id)?.name ?? '—' }}
                        <ChevronDown class="h-3 w-3" />
                      </span>
                    </template>
                    <template #label>{{ t('crm.changeStage') }}</template>
                      <DropdownMenuItem
                        v-for="stage in stages" :key="stage.id"
                        :disabled="!moveAllowed(stageOf(deal.stage_id), stage)"
                        :class="stage.id === deal.stage_id && 'bg-emerald-50/60 dark:bg-emerald-950/30'"
                        @click="moveDeal(deal, stage)"
                      >
                        {{ stage.name }}
                        <CheckCircle2 v-if="stage.id === deal.stage_id" class="ml-auto h-3.5 w-3.5 text-emerald-600" />
                      </DropdownMenuItem>
                  </DropdownMenu>
                  <Badge v-else :class="stageClass(stageOf(deal.stage_id))">
                    {{ stageOf(deal.stage_id)?.name ?? '—' }}
                  </Badge>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>

    <!-- Диалог новой сделки -->
    <Dialog :open="createOpen" :title="t('crm.newDeal')" @update:open="(v: boolean) => { if (!v) createOpen = false }">
      <form class="space-y-4" @submit.prevent="saveDeal">
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('crm.colDeal') }}</Label>
          <Input v-model="createForm.title" :placeholder="t('crm.dealTitlePlaceholder')" />
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('crm.colAmount') }}</Label>
            <Input v-model="createForm.amount" placeholder="150 000,00" inputmode="decimal" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('crm.colStage') }}</Label>
            <Select v-model="createForm.stageId" :options="openStages.map((s) => ({ value: s.id, label: s.name }))" />
          </div>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('crm.colCounterparty') }}</Label>
            <Select v-model="createForm.counterpartyId" :options="[
              { value: '', label: '—' },
              ...counterparties.map((k) => ({ value: k.id, label: k.name })),
            ]" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('crm.expectedClose') }}</Label>
            <Input v-model="createForm.expectedCloseAt" type="date" />
          </div>
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="createOpen = false">{{ t('permissions.cancel') }}</Button>
          <Button
            variant="emerald" type="submit" size="sm"
            :disabled="creating || !createForm.title.trim() || !isPositiveDecimalString(createForm.amount)"
          >{{ t('crm.save') }}</Button>
        </div>
      </form>
    </Dialog>

    <!-- Карточка сделки -->
    <Dialog
      :open="card !== null" :title="card?.title ?? ''" width="680px"
      @update:open="(v: boolean) => { if (!v) closeCard() }"
    >
      <div v-if="card" class="space-y-4">
        <!-- Шапка-сводка -->
        <div class="flex flex-wrap items-center gap-2">
          <Badge :class="stageClass(stageOf(card.stage_id))">{{ stageOf(card.stage_id)?.name }}</Badge>
          <Badge v-if="card.currency !== 'RUB'" variant="outline">{{ card.currency }}</Badge>
          <span class="text-sm font-semibold">{{ formatMoney2(card.amount, card.currency) }}</span>
          <span v-if="card.amount_base" class="text-xs text-muted-foreground">≈ {{ formatMoney2(card.amount_base) }}</span>
        </div>
        <div class="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm sm:grid-cols-3">
          <div><p class="text-[11px] text-muted-foreground">{{ t('crm.colCounterparty') }}</p><p>{{ card.counterparty_name ?? '—' }}</p></div>
          <div><p class="text-[11px] text-muted-foreground">{{ t('crm.colResponsible') }}</p><p>{{ card.responsible_name ?? '—' }}</p></div>
          <div><p class="text-[11px] text-muted-foreground">{{ t('crm.colProbability') }}</p><p>{{ stageOf(card.stage_id)?.probability ?? '—' }}%</p></div>
          <div><p class="text-[11px] text-muted-foreground">{{ t('crm.createdLabel') }}</p><p>{{ d(card.created_at, 'short') }}</p></div>
          <div v-if="card.expected_close_at"><p class="text-[11px] text-muted-foreground">{{ t('crm.expectedClose') }}</p><p>{{ card.expected_close_at }}</p></div>
          <div v-if="card.lost_reason"><p class="text-[11px] text-muted-foreground">{{ t('crm.lostReason') }}</p><p>{{ card.lost_reason }}</p></div>
        </div>
        <p v-if="stageOf(card.stage_id)?.is_lost" class="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950/40 dark:text-red-400">
          {{ t('crm.lostNote') }}
        </p>

        <!-- Коммуникации -->
        <div class="rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
          <p class="mb-3 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            <MessageSquare class="h-3.5 w-3.5" /> {{ t('crm.communications') }} ({{ communications.length }})
          </p>
          <ol class="space-y-2.5">
            <li v-if="communications.length === 0" class="text-sm text-muted-foreground">{{ t('crm.noCommunications') }}</li>
            <li v-for="c in communications" :key="c.id" class="flex items-start gap-2.5">
              <Badge variant="outline" class="mt-0.5 shrink-0 text-[10px]">{{ t(commKinds[c.kind] ?? 'crm.kindNote') }}</Badge>
              <div class="min-w-0 flex-1">
                <p class="text-[13px] leading-snug">{{ c.content }}</p>
                <p class="text-[11px] text-muted-foreground">{{ d(c.occurred_at ?? c.created_at, 'short') }}</p>
              </div>
            </li>
          </ol>
          <form v-if="canWrite" class="mt-3 flex flex-col gap-2 sm:flex-row" @submit.prevent="addCommunication">
            <div class="w-full sm:w-[130px]">
              <Select v-model="commForm.kind" :options="[
                { value: 'note', label: t('crm.kindNote') },
                { value: 'call', label: t('crm.kindCall') },
                { value: 'email', label: t('crm.kindEmail') },
                { value: 'meeting', label: t('crm.kindMeeting') },
              ]" />
            </div>
            <Input v-model="commForm.content" :placeholder="t('crm.commPlaceholder')" class="flex-1" />
            <Button variant="outline" type="submit" size="sm" :disabled="!commForm.content.trim()">
              {{ t('crm.add') }}
            </Button>
          </form>
        </div>

        <!-- Задачи -->
        <div class="rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
          <p class="mb-3 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            <ListChecks class="h-3.5 w-3.5" /> {{ t('crm.tasks') }} ({{ activities.length }})
          </p>
          <ul class="space-y-2">
            <li v-if="activities.length === 0" class="text-sm text-muted-foreground">{{ t('crm.noTasks') }}</li>
            <li v-for="a in activities" :key="a.id" class="flex items-center gap-2.5">
              <input
                v-if="canWrite" v-model="a.done" type="checkbox"
                class="h-4 w-4 rounded border-zinc-300 accent-emerald-600 dark:border-zinc-700"
                @change="toggleTask(a)"
              >
              <CheckCircle2 v-else :class="['h-4 w-4 shrink-0', a.done ? 'text-emerald-600' : 'text-zinc-300 dark:text-zinc-600']" />
              <span :class="['flex-1 text-sm', a.done && 'text-muted-foreground line-through']">{{ a.title }}</span>
              <span
                :class="['whitespace-nowrap text-[11px]',
                  !a.done && a.due_at && new Date(a.due_at) < new Date()
                    ? 'font-semibold text-red-600 dark:text-red-400' : 'text-muted-foreground']"
              >{{ a.due_at ? d(a.due_at, 'short') : '' }}</span>
            </li>
          </ul>
          <form v-if="canWrite" class="mt-3 flex flex-col gap-2 sm:flex-row" @submit.prevent="addTask">
            <Input v-model="taskForm.title" :placeholder="t('crm.taskPlaceholder')" class="flex-1" />
            <Input v-model="taskForm.dueAt" type="date" class="sm:w-[160px]" />
            <Button variant="outline" type="submit" size="sm" :disabled="!taskForm.title.trim() || !taskForm.dueAt">
              {{ t('crm.add') }}
            </Button>
          </form>
        </div>

        <!-- История изменений -->
        <details class="group rounded-xl border border-zinc-200 px-4 py-3 dark:border-zinc-800" open>
          <summary class="flex cursor-pointer list-none items-center justify-between text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {{ t('crm.history') }} ({{ history.length }})
            <ChevronDown class="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
          </summary>
          <ol class="relative mt-3 space-y-3 border-l border-zinc-200 pl-4 dark:border-zinc-800">
            <li v-if="history.length === 0" class="text-sm text-muted-foreground">{{ t('crm.noHistory') }}</li>
            <li v-for="(h, i) in history" :key="i" class="relative">
              <span class="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full bg-sky-500 ring-4 ring-background" />
              <p class="text-[13px] leading-snug">{{ historyLine(h.diff) }}</p>
              <p class="text-[11px] text-muted-foreground">{{ d(h.changed_at, 'short') }}<span v-if="h.reason"> · {{ h.reason }}</span></p>
            </li>
          </ol>
        </details>

        <!-- Футер -->
        <div class="flex flex-col gap-2 sm:flex-row sm:justify-between">
          <div class="flex gap-2">
            <Button variant="outline" size="sm" class="gap-1.5" @click="printOpen = true">
              <Printer class="h-3.5 w-3.5" /> {{ t('crm.print') }}
            </Button>
            <Button
              v-if="canWrite && stageOf(card.stage_id)?.is_won" variant="emerald" size="sm"
              class="gap-1.5" :title="t('crm.orderStubTitle')" @click="orderStub"
            >
              <ShoppingCart class="h-3.5 w-3.5" /> {{ t('crm.createOrder') }}
            </Button>
          </div>
          <Button variant="ghost" size="sm" @click="closeCard">{{ t('crm.close') }}</Button>
        </div>
      </div>
    </Dialog>

    <!-- Печать карточки (data-erp-print: на бумагу попадает только блок) -->
    <Dialog
      :open="printOpen" :title="t('crm.printTitle')" width="800px"
      @update:open="(v: boolean) => { if (!v) printOpen = false }"
    >
      <div v-if="card" class="space-y-4">
        <PrintArea>
          <div class="space-y-4 text-[13px] leading-relaxed text-zinc-900">
            <div class="flex items-start justify-between gap-4 border-b-2 border-zinc-900 pb-3">
              <div class="flex items-center gap-2.5">
                <QuasarMark class="h-9 w-9" />
                <div>
                  <p class="text-base font-bold">{{ t('brand.name') }} · {{ t('crm.dealCard') }}</p>
                  <p class="text-xs text-zinc-600">{{ t('crm.printed') }}: {{ new Date().toLocaleDateString('ru-RU') }}</p>
                </div>
              </div>
              <p class="text-right text-xs text-zinc-600">
                {{ t('crm.colStage') }}: <span class="font-semibold">{{ stageOf(card.stage_id)?.name }}</span><br>
                {{ t('crm.colProbability') }}: {{ stageOf(card.stage_id)?.probability ?? '—' }}%
              </p>
            </div>
            <table class="w-full">
              <tbody>
                <tr><td class="w-1/3 py-1 text-zinc-600">{{ t('crm.colDeal') }}</td><td class="py-1 font-medium">{{ card.title }}</td></tr>
                <tr><td class="py-1 text-zinc-600">{{ t('crm.colCounterparty') }}</td><td class="py-1">{{ card.counterparty_name ?? '—' }}</td></tr>
                <tr><td class="py-1 text-zinc-600">{{ t('crm.colResponsible') }}</td><td class="py-1">{{ card.responsible_name ?? '—' }}</td></tr>
                <tr><td class="py-1 text-zinc-600">{{ t('crm.created') }}</td><td class="py-1">{{ d(card.created_at, 'short') }}</td></tr>
                <tr v-if="card.expected_close_at"><td class="py-1 text-zinc-600">{{ t('crm.expectedClose') }}</td><td class="py-1">{{ card.expected_close_at }}</td></tr>
                <tr><td class="py-1 text-zinc-600">{{ t('crm.colAmount') }}</td><td class="py-1 font-semibold">{{ formatMoney2(card.amount, card.currency) }}<span v-if="card.amount_base"> ≈ {{ formatMoney2(card.amount_base) }}</span></td></tr>
                <tr v-if="card.lost_reason"><td class="py-1 text-zinc-600">{{ t('crm.lostReason') }}</td><td class="py-1">{{ card.lost_reason }}</td></tr>
              </tbody>
            </table>
            <div v-if="communications.length">
              <p class="mb-1.5 text-xs font-semibold uppercase tracking-wide text-zinc-600">{{ t('crm.communications') }}</p>
              <ul class="space-y-1">
                <li v-for="c in communications" :key="c.id">
                  <span class="text-zinc-600">[{{ t(commKinds[c.kind] ?? 'crm.kindNote') }}]</span>
                  {{ c.content }} — {{ d(c.occurred_at ?? c.created_at, 'short') }}
                </li>
              </ul>
            </div>
            <div v-if="activities.length">
              <p class="mb-1.5 text-xs font-semibold uppercase tracking-wide text-zinc-600">{{ t('crm.tasks') }}</p>
              <ul class="space-y-1">
                <li v-for="a in activities" :key="a.id">
                  {{ a.done ? '☑' : '☐' }} {{ a.title }} <span v-if="a.due_at" class="text-zinc-600">— {{ a.due_at }}</span>
                </li>
              </ul>
            </div>
            <p class="border-t border-zinc-300 pt-2 text-[11px] text-zinc-500">
              {{ t('crm.printFooter') }}
            </p>
          </div>
        </PrintArea>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="printOpen = false">{{ t('crm.close') }}</Button>
          <Button variant="emerald" size="sm" class="gap-1.5" @click="doPrint">
            <Printer class="h-3.5 w-3.5" /> {{ t('crm.print') }}
          </Button>
        </div>
      </div>
    </Dialog>
  </div>
</template>

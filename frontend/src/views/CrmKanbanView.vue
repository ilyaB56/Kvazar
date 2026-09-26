<script setup lang="ts">
// Канбан воронки продаж (PM-приоритет №1, макет task-kanban.tsx):
// колонки = стадии (/crm/stages по position), карточка сделки — название,
// сумма, контрагент, вероятность цветом; drag-n-drop → POST /crm/deals/
// {id}/move (move-правила won/lost соблюдаются); счётчики N и Σ в шапке
// колонки; клик — карточка сделки. Горизонтальный скролл на мобильных.
// DnD — @formkit/drag-and-drop: все колонки в одной группе, drop-валидация
// в accepts (move-правила), сервер остаётся источником истины.
import { computed, nextTick, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { dragAndDrop } from '@formkit/drag-and-drop/vue'
import { GripVertical } from 'lucide-vue-next'
import { get, post } from '../api/client'
import { Badge, Skeleton, useToast } from '../components/ui'
import type { PageOf } from '../components/ui'
import { useAuthStore } from '../stores/auth'
import { formatMoney2 } from '../utils/money'

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
  counterparty_name: string | null
  amount: string
  currency: string
}

const { t } = useI18n()
const router = useRouter()
const auth = useAuthStore()
const toast = useToast()

const loading = ref(true)
const stages = ref<Stage[]>([])
// сделки для отображения; source of truth по stage_id — сервер
const columnDeals = ref<Record<string, Deal[]>>({})
const canWrite = computed(() => auth.moduleLevel('crm') === 'rw')

const stageOf = (id: string) => stages.value.find((s) => s.id === id)

function moveAllowed(fromId: string, toId: string): boolean {
  const from = stageOf(fromId)
  const to = stageOf(toId)
  if (!from || !to || from.id === to.id) return false
  if ((from.is_won || from.is_lost) && (to.is_won || to.is_lost)) return false
  return true
}

async function loadBoard() {
  try {
    const [stageRows, page] = await Promise.all([
      get<Stage[]>('/crm/stages'),
      get<PageOf<Deal>>('/crm/deals?limit=0'),
    ])
    stages.value = [...stageRows].sort((a, b) => a.position - b.position)
    columnDeals.value = Object.fromEntries(stages.value.map((s) => [s.id, []]))
    for (const deal of page.items) {
      columnDeals.value[deal.stage_id]?.push(deal)
    }
    await finishLoading()
    return
  } catch (error) {
    toast.apiError(error)
    loading.value = false
  }
}

async function finishLoading() {
  // колонки рендерятся только после loading=false (v-else) — сначала
  // снимаем скелет, ждём патч DOM, затем монтируем DnD (иначе пусто)
  loading.value = false
  await nextTick()
  mountDnd()
}

// ---------- DnD ----------
const columnEls: Record<string, HTMLElement> = {}

function setColumnRef(stageId: string) {
  return (el: unknown) => {
    if (el instanceof HTMLElement) columnEls[stageId] = el
  }
}

function mountDnd() {
  const columns = stages.value
    .map((stage) => ({ stage, el: columnEls[stage.id] }))
    .filter((c): c is { stage: Stage; el: HTMLElement } => Boolean(c.el))
  dragAndDrop<Deal>(columns.map(({ stage, el }) => ({
    parent: el,
    values: columnDeals.value[stage.id],
    group: 'crm-funnel',
    dragHandle: '.kanban-handle',
    // move-правила won/lost (§ mini_crm.service): сервер остаётся истиной
    accepts: (target, initial, _current, state) => {
      // state — DragState/SynthSynthState с draggedNodes; на старте
      // инициализации может быть BaseDragState без узлов — пропускаем
      const nodes = (state as { draggedNodes?: Array<{ data?: { value?: unknown } }> })
        ?.draggedNodes
      const deal = (nodes?.[0]?.data?.value ?? null) as Deal | null
      if (!deal) return true
      return moveAllowed(deal.stage_id, stage.id)
    },
    transfer: (data: { draggedNodes?: Array<{ data?: { value?: unknown } }>;
                      targetParentData?: { parent?: HTMLElement } }) => {
      const deal = (data.draggedNodes?.[0]?.data?.value ?? null) as Deal | null
      const targetId = data.targetParentData?.parent?.dataset.stageId
      if (!deal || !targetId) return
      void commitMove(deal, targetId)
    },
  })))
}

async function commitMove(deal: Deal, targetStageId: string) {
  const from = stageOf(deal.stage_id)
  const to = stageOf(targetStageId)
  if (!from || !to || !moveAllowed(deal.stage_id, targetStageId)) {
    await loadBoard()  // откат визуала к серверному состоянию
    return
  }
  try {
    await post(`/crm/deals/${deal.id}/move`, { stage_id: targetStageId })
    toast.success(t('crm.moved', { stage: to.name }))
  } catch (error) {
    toast.apiError(error)
  }
  await loadBoard()  // перечитать: статусы/суммы из таблицы
}

onMounted(loadBoard)

// ---------- отображение ----------
// вероятность → цвет бейджа (как stageClass таблицы сделок)
const openTones = [
  'bg-sky-100 text-sky-800 dark:bg-sky-950/60 dark:text-sky-300',
  'bg-teal-100 text-teal-800 dark:bg-teal-950/60 dark:text-teal-300',
  'bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300',
  'bg-violet-100 text-violet-800 dark:bg-violet-950/60 dark:text-violet-300',
]
function probabilityCls(stage: Stage): string {
  if (stage.is_won) return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300'
  if (stage.is_lost) return 'bg-red-100 text-red-700 dark:bg-red-950/60 dark:text-red-300'
  const idx = stages.value.filter((s) => !s.is_won && !s.is_lost)
    .findIndex((s) => s.id === stage.id)
  return openTones[idx % openTones.length]
}

function columnSum(stageId: string): number {
  return (columnDeals.value[stageId] ?? [])
    .reduce((sum, d) => sum + Number(d.amount ?? 0), 0)
}
</script>

<template>
  <div class="space-y-3">
    <!-- горизонтальный скролл на мобильных; на десктопе — грид -->
    <div v-if="loading" class="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Skeleton v-for="i in 4" :key="i" class="h-64" />
    </div>
    <div v-else class="flex gap-3 overflow-x-auto pb-2 lg:grid lg:grid-cols-3 xl:grid-cols-4">
      <div
        v-for="stage in stages" :key="stage.id"
        class="flex w-[280px] shrink-0 flex-col gap-2 rounded-xl border border-dashed border-zinc-200 bg-zinc-50/50 p-2 dark:border-zinc-800 dark:bg-zinc-900/40 lg:w-auto lg:min-w-[220px]"
      >
        <!-- шапка: точка-стадия, название, N -->
        <p class="flex items-center gap-1.5 px-1 text-xs font-semibold">
          <span class="h-2 w-2 shrink-0 rounded-full"
                :class="stage.is_won ? 'bg-emerald-500' : stage.is_lost ? 'bg-red-400' : 'bg-sky-400'" />
          <span class="truncate">{{ stage.name }}</span>
          <span class="ml-auto shrink-0 rounded-full bg-zinc-200/70 px-1.5 text-[10px] font-bold text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
            {{ (columnDeals[stage.id] ?? []).length }}
          </span>
        </p>
        <p class="px-1 text-[11px] font-medium tabular-nums text-muted-foreground">
          Σ {{ formatMoney2(columnSum(stage.id).toFixed(2)) }}
          <span v-if="stage.probability != null" class="ml-1 opacity-70">· {{ stage.probability }}%</span>
        </p>

        <!-- карточки: контейнер DnD (data-stage-id для transfer) -->
        <div
          :ref="setColumnRef(stage.id)"
          :data-stage-id="stage.id"
          class="flex min-h-[120px] flex-col gap-2"
          :class="!canWrite && 'pointer-events-none'"
        >
          <div
            v-for="deal in columnDeals[stage.id]" :key="deal.id"
            :data-deal-id="deal.id"
            class="group cursor-pointer rounded-lg border bg-card p-2.5 shadow-sm transition-shadow hover:shadow-md dark:border-zinc-800"
            @click="router.push(`/crm/deals?deal=${deal.id}`)"
          >
            <div class="flex items-start gap-1.5">
              <GripVertical v-if="canWrite"
                            class="kanban-handle mt-0.5 h-3.5 w-3.5 shrink-0 cursor-grab text-zinc-300 transition-colors group-hover:text-zinc-400 active:cursor-grabbing dark:text-zinc-600" />
              <p class="flex-1 text-xs font-medium leading-snug"
                 :class="(stage.is_won || stage.is_lost) && 'text-muted-foreground'">
                {{ deal.title }}
              </p>
            </div>
            <div class="mt-2 flex flex-wrap items-center gap-1.5" :class="canWrite ? 'pl-5' : ''">
              <Badge :class="probabilityCls(stage)">
                {{ stage.is_won ? t('crm.stageWon') : stage.is_lost ? t('crm.stageLost') : (stage.probability ?? '—') + '%' }}
              </Badge>
              <span class="min-w-0 flex-1 truncate text-[10px] text-muted-foreground">
                {{ deal.counterparty_name || '—' }}
              </span>
              <span class="shrink-0 text-[10px] font-semibold tabular-nums">
                {{ formatMoney2(deal.amount) }}
              </span>
            </div>
          </div>
          <p v-if="!(columnDeals[stage.id] ?? []).length && canWrite"
             class="flex flex-1 items-center justify-center py-3 text-[11px] text-muted-foreground">
            {{ t('crm.kanbanDropHere') }}
          </p>
        </div>
      </div>
    </div>
  </div>
</template>

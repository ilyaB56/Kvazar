'use client'

import { useMemo, useState } from 'react'
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  closestCorners,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { dealsSeed, dealStages, fmtMoney, downloadCsv, type Deal, type DealStage } from '@/lib/erp-data'
import { CalendarClock, Flame, GripVertical, Target, TrendingUp, Download } from 'lucide-react'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'

function dealsWord(n: number): string {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return 'сделка'
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return 'сделки'
  return 'сделок'
}

function DealCard({ deal, dragging }: { deal: Deal; dragging?: boolean }) {
  return (
    <div
      className={cn(
        'group rounded-lg border bg-card p-3 shadow-sm transition-shadow dark:border-zinc-800',
        dragging ? 'rotate-2 shadow-xl ring-2 ring-emerald-500/40' : 'hover:shadow-md'
      )}
    >
      <div className="flex items-start gap-1.5">
        <GripVertical className="mt-0.5 h-3.5 w-3.5 shrink-0 cursor-grab text-zinc-300 transition-colors group-hover:text-zinc-400 active:cursor-grabbing dark:text-zinc-600" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-semibold leading-snug">{deal.company}</p>
          <p className="mt-0.5 whitespace-nowrap text-[13px] font-bold text-emerald-700 dark:text-emerald-400">{fmtMoney(deal.amount)}</p>
        </div>
        <AvatarFallbackish initials={deal.initials} tone={deal.tone} />
      </div>

      {/* Вероятность */}
      <div className="mt-2 flex items-center gap-2 pl-5">
        <Progress value={deal.probability} className="h-1.5 flex-1 [&>div]:bg-emerald-500" />
        <span className="w-8 text-right text-[10px] font-semibold text-muted-foreground">{deal.probability}%</span>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 pl-5 text-[10px] text-muted-foreground">
        <span className="flex items-center gap-1">
          <CalendarClock className="h-2.5 w-2.5" /> {deal.days} дн. в этапе
        </span>
        <span className="text-zinc-300 dark:text-zinc-700">·</span>
        <span className="truncate">{deal.next}</span>
      </div>

      <div className="mt-2 flex items-center justify-between pl-5">
        <Badge variant="outline" className="px-1.5 py-0 text-[10px] font-normal text-muted-foreground">
          {deal.manager}
        </Badge>
        {deal.days >= 5 && (
          <Badge variant="outline" className="gap-0.5 border-orange-200 bg-orange-50 px-1.5 py-0 text-[10px] text-orange-600 dark:border-orange-900 dark:bg-orange-950/60 dark:text-orange-400">
            <Flame className="h-2.5 w-2.5" /> давно
          </Badge>
        )}
      </div>
    </div>
  )
}

function AvatarFallbackish({ initials, tone }: { initials: string; tone: string }) {
  return (
    <span className={cn('flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[9px] font-bold', tone)}>
      {initials}
    </span>
  )
}

function DraggableDeal({ deal }: { deal: Deal }) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: deal.id })
  return (
    <div ref={setNodeRef} {...attributes} {...listeners} className={cn('touch-none', isDragging && 'opacity-30')}>
      <DealCard deal={deal} />
    </div>
  )
}

function StageColumn({ stage, deals }: { stage: (typeof dealStages)[number]; deals: Deal[] }) {
  const { setNodeRef, isOver } = useDroppable({ id: stage.id })
  const total = deals.reduce((s, d) => s + d.amount, 0)
  return (
    <div
      ref={setNodeRef}
      className={cn(
        'flex min-h-[180px] flex-col gap-2 rounded-xl border border-dashed p-2 transition-colors dark:border-zinc-800',
        isOver ? 'border-emerald-400 bg-emerald-50/60 dark:bg-emerald-950/30' : 'border-zinc-200 bg-zinc-50/50 dark:bg-zinc-900/40'
      )}
    >
      <div className="px-1">
        <p className={cn('flex items-center gap-1.5 text-xs font-semibold', stage.tone)}>
          <span className={cn('h-2 w-2 rounded-full', stage.dot)} />
          {stage.title}
          <span className="ml-auto rounded-full bg-zinc-200/70 px-1.5 text-[10px] font-bold text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
            {deals.length}
          </span>
        </p>
        <p className="mt-0.5 pl-4 text-[10px] font-medium text-muted-foreground">{fmtMoney(total)}</p>
      </div>
      {deals.map((d) => (
        <DraggableDeal key={d.id} deal={d} />
      ))}
      {deals.length === 0 && (
        <p className="flex flex-1 items-center justify-center py-4 text-center text-[11px] text-muted-foreground">
          Перетащите сделку сюда
        </p>
      )}
    </div>
  )
}

export function SalesFunnel() {
  const [deals, setDeals] = useState<Deal[]>(dealsSeed)
  const [activeId, setActiveId] = useState<string | null>(null)
  const { toast } = useToast()

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }))

  const stats = useMemo(() => {
    const total = deals.reduce((s, d) => s + d.amount, 0)
    const won = deals.filter((d) => d.stage === 'won')
    const wonSum = won.reduce((s, d) => s + d.amount, 0)
    const weighted = deals.reduce((s, d) => s + (d.amount * d.probability) / 100, 0)
    return { total, wonCount: won.length, wonSum, weighted }
  }, [deals])

  const onDragStart = (e: DragStartEvent) => setActiveId(String(e.active.id))

  const onDragEnd = (e: DragEndEvent) => {
    setActiveId(null)
    const { active, over } = e
    if (!over) return
    const dealId = String(active.id)
    const target = String(over.id) as DealStage
    const deal = deals.find((d) => d.id === dealId)
    setDeals((prev) => prev.map((d) => (d.id === dealId ? { ...d, stage: target } : d)))
    if (deal && deal.stage !== target) {
      const stageTitle = dealStages.find((s) => s.id === target)?.title ?? target
      toast({
        title: 'Сделка перемещена',
        description: `«${deal.company}» → ${stageTitle}`,
        duration: 2500,
      })
    }
  }

  const activeDeal = deals.find((d) => d.id === activeId) ?? null

  const exportFunnel = () => {
    downloadCsv(
      'vronka-sdelok.csv',
      deals.map((d) => ({
        company: d.company,
        stage: dealStages.find((s) => s.id === d.stage)?.title ?? d.stage,
        amount: d.amount,
        probability: d.probability,
        manager: d.manager,
        days: d.days,
        next: d.next,
      })),
      [
        { key: 'company', label: 'Компания' },
        { key: 'stage', label: 'Этап' },
        { key: 'amount', label: 'Сумма, ₽' },
        { key: 'probability', label: 'Вероятность, %' },
        { key: 'manager', label: 'Менеджер' },
        { key: 'days', label: 'Дней в этапе' },
        { key: 'next', label: 'Следующий шаг' },
      ]
    )
    toast({
      title: 'Воронка выгружена',
      description: `${deals.length} сделок · CSV для Excel в загрузках`,
      duration: 3000,
    })
  }

  return (
    <div className="space-y-4">
      {/* Сводка по воронке */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="flex items-center gap-3 rounded-xl border border-zinc-200 bg-card p-4 shadow-sm dark:border-zinc-800">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-emerald-100 dark:bg-emerald-950">
            <Target className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
          </span>
          <div className="min-w-0">
            <p className="text-xs text-muted-foreground">Сумма в воронке</p>
            <p className="truncate text-sm font-bold">{fmtMoney(stats.total)}</p>
          </div>
        </div>
        <div className="flex items-center gap-3 rounded-xl border border-zinc-200 bg-card p-4 shadow-sm dark:border-zinc-800">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-teal-100 dark:bg-teal-950">
            <TrendingUp className="h-4 w-4 text-teal-600 dark:text-teal-400" />
          </span>
          <div className="min-w-0">
            <p className="text-xs text-muted-foreground">Прогноз (взвешенный)</p>
            <p className="truncate text-sm font-bold">{fmtMoney(stats.weighted)}</p>
          </div>
        </div>
        <div className="flex items-center gap-3 rounded-xl border border-zinc-200 bg-card p-4 shadow-sm dark:border-zinc-800">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-amber-100 dark:bg-amber-950">
            <Flame className="h-4 w-4 text-amber-600 dark:text-amber-400" />
          </span>
          <div className="min-w-0">
            <p className="text-xs text-muted-foreground">Выиграно за декабрь</p>
            <p className="truncate text-sm font-bold">
              {stats.wonCount} {dealsWord(stats.wonCount)} · {fmtMoney(stats.wonSum)}
            </p>
          </div>
        </div>
      </div>

      {/* Экспорт воронки */}
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          Всего в работе: <span className="font-semibold text-foreground">{deals.length}</span>{' '}
          {dealsWord(deals.length)} · перетащите карточку, чтобы сменить этап
        </p>
        <Button variant="outline" size="sm" className="h-8 shrink-0 gap-1.5" onClick={exportFunnel}>
          <Download className="h-3.5 w-3.5" /> Экспорт CSV
        </Button>
      </div>

      {/* Канбан сделок */}
      <DndContext sensors={sensors} collisionDetection={closestCorners} onDragStart={onDragStart} onDragEnd={onDragEnd}>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {dealStages.map((s) => (
            <StageColumn key={s.id} stage={s} deals={deals.filter((d) => d.stage === s.id)} />
          ))}
        </div>
        <DragOverlay>
          {activeDeal && (
            <div className="w-64">
              <DealCard deal={activeDeal} dragging />
            </div>
          )}
        </DragOverlay>
      </DndContext>
    </div>
  )
}

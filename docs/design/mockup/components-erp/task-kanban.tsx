'use client'

import { useState } from 'react'
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
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { kanbanSeed, type KanbanTask, type TaskColumn } from '@/lib/erp-data'
import { CalendarDays, GripVertical, KanbanSquare, Plus } from 'lucide-react'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'

const columns: { id: TaskColumn; title: string; tone: string; dot: string }[] = [
  { id: 'todo', title: 'К выполнению', tone: 'text-zinc-600 dark:text-zinc-300', dot: 'bg-zinc-400' },
  { id: 'doing', title: 'В работе', tone: 'text-amber-600 dark:text-amber-400', dot: 'bg-amber-500' },
  { id: 'done', title: 'Готово', tone: 'text-emerald-600 dark:text-emerald-400', dot: 'bg-emerald-500' },
]

const priorityMap: Record<KanbanTask['priority'], { label: string; className: string }> = {
  high: { label: 'Срочно', className: 'border-red-200 bg-red-50 text-red-600 dark:bg-red-950/60 dark:text-red-400 dark:border-red-900' },
  medium: { label: 'Средний', className: 'border-amber-200 bg-amber-50 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300 dark:border-amber-900' },
  low: { label: 'Низкий', className: 'border-zinc-200 bg-zinc-50 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400 dark:border-zinc-700' },
}

function TaskCard({ task, dragging }: { task: KanbanTask; dragging?: boolean }) {
  return (
    <div
      className={cn(
        'group rounded-lg border bg-card p-2.5 shadow-sm transition-shadow dark:border-zinc-800',
        dragging ? 'rotate-2 shadow-xl ring-2 ring-emerald-500/40' : 'hover:shadow-md'
      )}
    >
      <div className="flex items-start gap-1.5">
        <GripVertical className="mt-0.5 h-3.5 w-3.5 shrink-0 cursor-grab text-zinc-300 transition-colors group-hover:text-zinc-400 active:cursor-grabbing dark:text-zinc-600" />
        <p className={cn('flex-1 text-xs font-medium leading-snug', task.column === 'done' && 'text-muted-foreground line-through')}>
          {task.title}
        </p>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 pl-5">
        <Badge variant="outline" className={cn('px-1.5 py-0 text-[10px]', priorityMap[task.priority].className)}>
          {priorityMap[task.priority].label}
        </Badge>
        <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
          <CalendarDays className="h-2.5 w-2.5" /> {task.due} · {task.assignee}
        </span>
      </div>
    </div>
  )
}

function DraggableTask({ task }: { task: KanbanTask }) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: task.id })
  return (
    <div ref={setNodeRef} {...attributes} {...listeners} className={cn('touch-none', isDragging && 'opacity-30')}>
      <TaskCard task={task} />
    </div>
  )
}

function Column({
  col,
  tasks,
}: {
  col: (typeof columns)[number]
  tasks: KanbanTask[]
}) {
  const { setNodeRef, isOver } = useDroppable({ id: col.id })
  return (
    <div
      ref={setNodeRef}
      className={cn(
        'flex min-h-[120px] flex-col gap-2 rounded-xl border border-dashed p-2 transition-colors dark:border-zinc-800',
        isOver ? 'border-emerald-400 bg-emerald-50/60 dark:bg-emerald-950/30' : 'border-zinc-200 bg-zinc-50/50 dark:bg-zinc-900/40'
      )}
    >
      <p className={cn('flex items-center gap-1.5 px-1 text-xs font-semibold', col.tone)}>
        <span className={cn('h-2 w-2 rounded-full', col.dot)} />
        {col.title}
        <span className="ml-auto rounded-full bg-zinc-200/70 px-1.5 text-[10px] font-bold text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
          {tasks.length}
        </span>
      </p>
      {tasks.map((t) => (
        <DraggableTask key={t.id} task={t} />
      ))}
      {tasks.length === 0 && (
        <p className="flex flex-1 items-center justify-center py-3 text-[11px] text-muted-foreground">
          Перетащите задачу сюда
        </p>
      )}
    </div>
  )
}

export function TaskKanban() {
  const [tasks, setTasks] = useState<KanbanTask[]>(kanbanSeed)
  const [activeId, setActiveId] = useState<string | null>(null)
  const { toast } = useToast()

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }))

  const onDragStart = (e: DragStartEvent) => setActiveId(String(e.active.id))

  const onDragEnd = (e: DragEndEvent) => {
    setActiveId(null)
    const { active, over } = e
    if (!over) return
    const taskId = String(active.id)
    const targetCol = String(over.id) as TaskColumn
    const task = tasks.find((t) => t.id === taskId)
    setTasks((prev) =>
      prev.map((t) => (t.id === taskId ? { ...t, column: targetCol } : t))
    )
    if (task && task.column !== targetCol) {
      const colTitle = columns.find((c) => c.id === targetCol)?.title ?? targetCol
      toast({
        title: 'Задача перемещена',
        description: `«${task.title.slice(0, 32)}${task.title.length > 32 ? '…' : ''}» → ${colTitle}`,
        duration: 2500,
      })
    }
  }

  const activeTask = tasks.find((t) => t.id === activeId) ?? null

  return (
    <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <KanbanSquare className="h-4 w-4 text-emerald-600" />
            Задачи на неделю
          </CardTitle>
          <CardDescription>Перетаскивайте задачи между колонками</CardDescription>
        </div>
        <Badge variant="secondary">{tasks.filter((t) => t.column !== 'done').length} активных</Badge>
      </CardHeader>
      <CardContent>
        <DndContext
          sensors={sensors}
          collisionDetection={closestCorners}
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
        >
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {columns.map((c) => (
              <Column key={c.id} col={c} tasks={tasks.filter((t) => t.column === c.id)} />
            ))}
          </div>
          <DragOverlay>
            {activeTask && (
              <div className="w-64">
                <TaskCard task={activeTask} dragging />
              </div>
            )}
          </DragOverlay>
        </DndContext>
        <button className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-zinc-300 py-2 text-xs text-muted-foreground transition-colors hover:border-emerald-400 hover:bg-emerald-50/50 hover:text-emerald-700 dark:border-zinc-700 dark:hover:border-emerald-700 dark:hover:bg-emerald-950/30 dark:hover:text-emerald-400">
          <Plus className="h-3.5 w-3.5" /> Добавить задачу
        </button>
      </CardContent>
    </Card>
  )
}

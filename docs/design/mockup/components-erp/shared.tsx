'use client'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import {
  PackageCheck,
  Clock3,
  Truck,
  CheckCircle2,
  XCircle,
  CircleDashed,
  Wallet,
  AlertTriangle,
  FileEdit,
} from 'lucide-react'
import type { OrderStatus, InvoiceStatus, StockLevel } from '@/lib/erp-data'

// ---------- Статусы заказов ----------
export const orderStatusMap: Record<OrderStatus, { label: string; className: string; icon: React.ReactNode }> = {
  new: { label: 'Новый', className: 'bg-sky-100 text-sky-700 border-sky-200 dark:bg-sky-950/60 dark:text-sky-300 dark:border-sky-900', icon: <CircleDashed className="h-3 w-3" /> },
  processing: { label: 'В работе', className: 'bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-950/60 dark:text-amber-300 dark:border-amber-900', icon: <Clock3 className="h-3 w-3" /> },
  shipping: { label: 'Отгрузка', className: 'bg-teal-100 text-teal-700 border-teal-200 dark:bg-teal-950/60 dark:text-teal-300 dark:border-teal-900', icon: <Truck className="h-3 w-3" /> },
  done: { label: 'Выполнен', className: 'bg-emerald-100 text-emerald-700 border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-900', icon: <CheckCircle2 className="h-3 w-3" /> },
  cancelled: { label: 'Отменён', className: 'bg-red-100 text-red-600 border-red-200 dark:bg-red-950/60 dark:text-red-400 dark:border-red-900', icon: <XCircle className="h-3 w-3" /> },
}

export function OrderStatusBadge({ status }: { status: OrderStatus }) {
  const s = orderStatusMap[status]
  return (
    <Badge variant="outline" className={cn('gap-1 font-medium', s.className)}>
      {s.icon}
      {s.label}
    </Badge>
  )
}

// ---------- Статусы счетов ----------
export const invoiceStatusMap: Record<InvoiceStatus, { label: string; className: string }> = {
  paid: { label: 'Оплачен', className: 'bg-emerald-100 text-emerald-700 border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-900' },
  pending: { label: 'Ожидает', className: 'bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-950/60 dark:text-amber-300 dark:border-amber-900' },
  overdue: { label: 'Просрочен', className: 'bg-red-100 text-red-600 border-red-200 dark:bg-red-950/60 dark:text-red-400 dark:border-red-900' },
  draft: { label: 'Черновик', className: 'bg-zinc-100 text-zinc-600 border-zinc-200 dark:bg-zinc-800 dark:text-zinc-400 dark:border-zinc-700' },
}

export function InvoiceStatusBadge({ status }: { status: InvoiceStatus }) {
  const s = invoiceStatusMap[status]
  return (
    <Badge variant="outline" className={cn('font-medium', s.className)}>
      {s.label}
    </Badge>
  )
}

// ---------- Уровень запасов ----------
export function stockLevelOf(qty: number, minQty: number, capacity: number): StockLevel {
  if (minQty === 0) return 'ok'
  if (qty < minQty * 0.5) return 'critical'
  if (qty < minQty) return 'low'
  if (capacity > 0 && qty > capacity) return 'overflow'
  return 'ok'
}

export const stockLevelMap: Record<StockLevel, { label: string; className: string; icon: React.ReactNode }> = {
  ok: { label: 'В норме', className: 'bg-emerald-100 text-emerald-700 border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-900', icon: <PackageCheck className="h-3 w-3" /> },
  low: { label: 'Мало', className: 'bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-950/60 dark:text-amber-300 dark:border-amber-900', icon: <AlertTriangle className="h-3 w-3" /> },
  critical: { label: 'Критично', className: 'bg-red-100 text-red-600 border-red-200 dark:bg-red-950/60 dark:text-red-400 dark:border-red-900', icon: <AlertTriangle className="h-3 w-3" /> },
  overflow: { label: 'Избыток', className: 'bg-sky-100 text-sky-700 border-sky-200 dark:bg-sky-950/60 dark:text-sky-300 dark:border-sky-900', icon: <Wallet className="h-3 w-3" /> },
}

export function StockLevelBadge({ level }: { level: StockLevel }) {
  const s = stockLevelMap[level]
  return (
    <Badge variant="outline" className={cn('gap-1 font-medium', s.className)}>
      {s.icon}
      {s.label}
    </Badge>
  )
}

// ---------- Заголовок экрана ----------
export function ViewHeader({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle?: string
  children?: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-foreground sm:text-2xl">{title}</h1>
        {subtitle && <p className="text-sm text-muted-foreground mt-0.5">{subtitle}</p>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  )
}

// ---------- Дельта KPI ----------
export function DeltaPill({ delta, invert = false }: { delta: number; invert?: boolean }) {
  const positive = invert ? delta < 0 : delta > 0
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-xs font-semibold',
        positive
          ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300'
          : 'bg-red-100 text-red-600 dark:bg-red-950/60 dark:text-red-400'
      )}
    >
      {delta > 0 ? '▲' : '▼'} {Math.abs(delta).toFixed(1)}%
    </span>
  )
}

// ---------- Пометка макета ----------
export function DraftNote({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-dashed border-zinc-300 bg-zinc-50 px-3 py-2 text-xs text-muted-foreground dark:border-zinc-700 dark:bg-zinc-900/60">
      <FileEdit className="h-3.5 w-3.5 shrink-0" />
      {text}
    </div>
  )
}

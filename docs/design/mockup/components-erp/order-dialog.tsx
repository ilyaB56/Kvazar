'use client'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { OrderStatusBadge } from './shared'
import { OrderPrintView } from './order-print-view'
import {
  getOrderItems,
  orderTimelineSteps,
  getOrderAudit,
  auditToneMap,
  fmtMoney,
  type OrderRow,
} from '@/lib/erp-data'
import { CheckCircle2, Circle, Printer, Truck, X, ChevronDown } from 'lucide-react'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'
import { useState } from 'react'

export function OrderDialog({
  order,
  onClose,
}: {
  order: OrderRow | null
  onClose: () => void
}) {
  const { toast } = useToast()
  const items = order ? getOrderItems(order) : []
  const delivery = order && order.status !== 'draft' ? 24_500 : 0
  const audit = order ? getOrderAudit(order) : []
  // Печатная форма (Торг-12) открывается поверх карточки заказа
  const [printOpen, setPrintOpen] = useState(false)

  const showToast = (title: string, description: string) =>
    toast({ title, description, duration: 3000 })

  return (
    <>
    <Dialog open={!!order} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl erp-scroll">
        {order && (
          <>
            <DialogHeader>
              <div className="flex flex-wrap items-center gap-2 pr-6">
                <DialogTitle className="text-lg">Заказ {order.id}</DialogTitle>
                <OrderStatusBadge status={order.status} />
                <Badge variant="outline" className="font-normal text-muted-foreground">
                  {order.channel}
                </Badge>
              </div>
              <DialogDescription>
                {order.customer} · оформлен {order.date} · менеджер {order.manager}
              </DialogDescription>
            </DialogHeader>

            {/* Хронология */}
            <div className="rounded-xl border p-4 dark:border-zinc-800">
              <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Хронология заказа
              </p>
              <ol className="space-y-3">
                {orderTimelineSteps.map((step) => {
                  const reached = step.key.includes(order.status)
                  return (
                    <li key={step.label} className="flex items-start gap-3">
                      {reached ? (
                        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                      ) : (
                        <Circle className="mt-0.5 h-4 w-4 shrink-0 text-zinc-300 dark:text-zinc-600" />
                      )}
                      <div>
                        <p className={cn('text-sm font-medium', !reached && 'text-muted-foreground')}>
                          {step.label}
                        </p>
                        <p className="text-xs text-muted-foreground">{step.desc}</p>
                      </div>
                    </li>
                  )
                })}
              </ol>
              {order.status === 'cancelled' && (
                <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950/40 dark:text-red-400">
                  Заказ отменён клиентом 16.12.2025 · причина: изменение бюджета
                </p>
              )}
            </div>

            {/* История изменений (аудит-трейл) */}
            <details
              className="group rounded-xl border px-4 py-3 open:pb-4 dark:border-zinc-800"
              open
            >
              <summary className="flex cursor-pointer list-none items-center justify-between text-xs font-semibold uppercase tracking-wide text-muted-foreground transition-colors hover:text-foreground [&::-webkit-details-marker]:hidden">
                История изменений · аудит ({audit.length})
                <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
              </summary>
              <ol className="relative mt-3 space-y-3 border-l border-zinc-200 pl-4 dark:border-zinc-800">
                {audit.map((e, i) => {
                  const tone = auditToneMap[e.tone]
                  return (
                    <li key={i} className="relative">
                      <span
                        className={cn(
                          'absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full ring-4 ring-background',
                          tone.dot
                        )}
                      />
                      <p className="text-[13px] leading-snug">
                        <span className="font-medium">{e.user}</span>{' '}
                        <span className="text-muted-foreground">— {e.action}</span>
                      </p>
                      <p className={cn('text-[11px]', tone.text)}>{e.time}</p>
                    </li>
                  )
                })}
              </ol>
            </details>

            {/* Позиции */}
            <div className="overflow-hidden rounded-xl border dark:border-zinc-800">
              <Table>
                <TableHeader>
                  <TableRow className="bg-zinc-50/80 hover:bg-zinc-50/80 dark:bg-zinc-900/50">
                    <TableHead>Артикул</TableHead>
                    <TableHead>Позиция</TableHead>
                    <TableHead className="text-right">Кол-во</TableHead>
                    <TableHead className="text-right">Сумма</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((it) => (
                    <TableRow key={it.sku}>
                      <TableCell className="font-mono text-xs text-muted-foreground">{it.sku}</TableCell>
                      <TableCell className="text-sm">{it.name}</TableCell>
                      <TableCell className="text-right text-sm">{it.qty}</TableCell>
                      <TableCell className="text-right text-sm font-medium whitespace-nowrap">
                        {fmtMoney(it.qty * it.price)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>

            {/* Итоги */}
            <div className="ml-auto w-full max-w-[300px] space-y-1.5 text-sm">
              <div className="flex justify-between text-muted-foreground">
                <span>Товары ({order.items} поз.)</span>
                <span>{fmtMoney(order.amount - delivery)}</span>
              </div>
              <div className="flex justify-between text-muted-foreground">
                <span>Доставка</span>
                <span>{fmtMoney(delivery)}</span>
              </div>
              <Separator />
              <div className="flex justify-between text-base font-bold">
                <span>Итого</span>
                <span className="text-emerald-700 dark:text-emerald-400">{fmtMoney(order.amount)}</span>
              </div>
            </div>
            <p className="text-center text-[11px] text-muted-foreground">
              Состав позиций демонстрационный · детализация — в печатной форме Торг-12
            </p>

            <DialogFooter className="gap-2 sm:gap-0">
              <div className="flex w-full flex-col gap-2 sm:flex-row sm:justify-between">
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="gap-1.5"
                    onClick={() => setPrintOpen(true)}
                  >
                    <Printer className="h-3.5 w-3.5" /> Печать
                  </Button>
                  {order.status !== 'done' && order.status !== 'cancelled' && (
                    <Button
                      size="sm"
                      className="gap-1.5 bg-emerald-600 text-white hover:bg-emerald-700"
                      onClick={() =>
                        showToast('Отгрузка запланирована', `Заказ ${order.id} передан на склад · экспедитор узнает за час`)
                      }
                    >
                      <Truck className="h-3.5 w-3.5" /> Отгрузить
                    </Button>
                  )}
                </div>
                <Button variant="ghost" size="sm" className="gap-1.5" onClick={onClose}>
                  <X className="h-3.5 w-3.5" /> Закрыть
                </Button>
              </div>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>

    {/* Печатная форма Торг-12 (открывается поверх карточки) */}
    <OrderPrintView
      order={printOpen ? order : null}
      open={printOpen}
      onOpenChange={setPrintOpen}
    />
    </>
  )
}

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
import { Progress } from '@/components/ui/progress'
import { Separator } from '@/components/ui/separator'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { StockLevelBadge, stockLevelOf } from './shared'
import { getStockItemDetails, movementKindMap, fmtMoney, fmtNum, type StockRow } from '@/lib/erp-data'
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  Boxes,
  CalendarClock,
  Factory,
  Layers,
  MapPin,
  PackageCheck,
  Printer,
  ScanBarcode,
  X,
} from 'lucide-react'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'

export function ItemDialog({ item, onClose }: { item: StockRow | null; onClose: () => void }) {
  const { toast } = useToast()
  const d = item ? getStockItemDetails(item) : null
  const pct = item ? Math.min(100, Math.round((item.qty / item.capacity) * 100)) : 0
  const available = item ? item.qty - (d?.reserved ?? 0) : 0
  const level = item ? stockLevelOf(item.qty, item.minQty, item.capacity) : 'ok'

  const showToast = (title: string, description: string) =>
    toast({ title, description, duration: 3000 })

  return (
    <Dialog open={!!item} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl erp-scroll">
        {item && d && (
          <>
            <DialogHeader>
              <div className="flex flex-wrap items-center gap-2 pr-6">
                <DialogTitle className="text-lg">{item.name}</DialogTitle>
                <StockLevelBadge level={level} />
              </div>
              <DialogDescription className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="inline-flex items-center gap-1 font-mono text-xs">
                  <ScanBarcode className="h-3.5 w-3.5" /> {item.sku}
                </span>
                <span className="text-zinc-300 dark:text-zinc-700">|</span>
                <span>{item.category}</span>
                <span className="text-zinc-300 dark:text-zinc-700">|</span>
                <span className="inline-flex items-center gap-1">
                  <MapPin className="h-3.5 w-3.5" /> {item.warehouse} · ячейка {d.shelf}
                </span>
              </DialogDescription>
            </DialogHeader>

            {/* Стат-плитки */}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div className="rounded-xl border p-3 dark:border-zinc-800">
                <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  <Boxes className="h-3.5 w-3.5" /> Остаток
                </p>
                <p className="mt-1 text-lg font-bold tabular-nums">{fmtNum(item.qty)}</p>
                <p className="text-[11px] text-muted-foreground">мин. {item.minQty}</p>
              </div>
              <div className="rounded-xl border p-3 dark:border-zinc-800">
                <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  <PackageCheck className="h-3.5 w-3.5" /> Резерв
                </p>
                <p className="mt-1 text-lg font-bold tabular-nums text-amber-600 dark:text-amber-400">{d.reserved}</p>
                <p className="text-[11px] text-muted-foreground">свободно {Math.max(0, available)}</p>
              </div>
              <div className="rounded-xl border p-3 dark:border-zinc-800">
                <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  <ArrowDownToLine className="h-3.5 w-3.5" /> В пути
                </p>
                <p className={cn('mt-1 text-lg font-bold tabular-nums', d.incoming > 0 ? 'text-emerald-600 dark:text-emerald-400' : '')}>
                  {d.incoming > 0 ? `+${d.incoming}` : '—'}
                </p>
                <p className="text-[11px] text-muted-foreground">заказано у поставщика</p>
              </div>
              <div className="rounded-xl border p-3 dark:border-zinc-800">
                <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  <CalendarClock className="h-3.5 w-3.5" /> Срок поставки
                </p>
                <p className="mt-1 text-lg font-bold tabular-nums">{d.leadDays} дн.</p>
                <p className="text-[11px] text-muted-foreground">с момента заказа</p>
              </div>
            </div>

            {/* Заполненность */}
            <div className="rounded-xl border p-4 dark:border-zinc-800">
              <div className="mb-2 flex items-center justify-between text-sm">
                <span className="font-medium">Заполненность ёмкости</span>
                <span className="text-xs text-muted-foreground">
                  {fmtNum(item.qty)} / {fmtNum(item.capacity)} · {pct}%
                </span>
              </div>
              <Progress
                value={pct}
                className={cn(
                  'h-2.5 [&>div]:bg-emerald-500',
                  level === 'critical' && '[&>div]:bg-red-500',
                  level === 'low' && '[&>div]:bg-amber-500'
                )}
              />
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              {/* Где хранится */}
              <div className="rounded-xl border p-4 dark:border-zinc-800">
                <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Где хранится
                </p>
                <div className="space-y-3">
                  {d.locations.map((l) => {
                    const share = item.qty > 0 ? Math.round((l.qty / item.qty) * 100) : 100
                    return (
                      <div key={l.warehouse}>
                        <div className="mb-1 flex items-center justify-between text-sm">
                          <span className="font-medium">{l.warehouse}</span>
                          <span className="tabular-nums text-muted-foreground">{fmtNum(l.qty)} шт · {share}%</span>
                        </div>
                        <Progress value={share} className="h-1.5 [&>div]:bg-teal-500" />
                      </div>
                    )
                  })}
                </div>
                <Separator className="my-3" />
                <p className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Factory className="h-3.5 w-3.5 shrink-0" />
                  Поставщик: <span className="font-medium text-foreground">{d.supplier}</span>
                </p>
              </div>

              {/* Стоимость */}
              <div className="rounded-xl border p-4 dark:border-zinc-800">
                <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Стоимость запаса
                </p>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Цена единицы</span>
                    <span className="font-medium tabular-nums">{fmtMoney(item.price)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Остаток × цена</span>
                    <span className="font-medium tabular-nums">{fmtMoney(item.qty * item.price)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">В резерве</span>
                    <span className="font-medium tabular-nums">{fmtMoney(d.reserved * item.price)}</span>
                  </div>
                  <Separator />
                  <div className="flex justify-between">
                    <span className="flex items-center gap-1.5 text-muted-foreground">
                      <Layers className="h-3.5 w-3.5" /> Доступно к продаже
                    </span>
                    <span className="font-bold tabular-nums text-emerald-700 dark:text-emerald-400">
                      {fmtMoney(Math.max(0, available) * item.price)}
                    </span>
                  </div>
                </div>
                <p className="mt-3 text-[11px] leading-snug text-muted-foreground">
                  Обновлено {item.updated} · цены без НДС, списание по средней скользящей
                </p>
              </div>
            </div>

            {/* История движений */}
            <div className="overflow-hidden rounded-xl border dark:border-zinc-800">
              <p className="border-b bg-zinc-50/80 px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground dark:bg-zinc-900/50">
                Последние движения
              </p>
              <Table>
                <TableHeader>
                  <TableRow className="bg-zinc-50/50 hover:bg-zinc-50/50 dark:bg-transparent">
                    <TableHead className="w-[70px]">Дата</TableHead>
                    <TableHead>Тип</TableHead>
                    <TableHead>Документ</TableHead>
                    <TableHead className="hidden sm:table-cell">Кто</TableHead>
                    <TableHead className="text-right">Кол-во</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {d.movements.map((m) => {
                    const kind = movementKindMap[m.kind]
                    return (
                      <TableRow key={m.id}>
                        <TableCell className="text-xs text-muted-foreground">{m.date}</TableCell>
                        <TableCell>
                          <Badge variant="outline" className={cn('font-normal', kind.cls)}>
                            {m.kind === 'in' && <ArrowDownToLine className="mr-1 h-3 w-3" />}
                            {m.kind === 'out' && <ArrowUpFromLine className="mr-1 h-3 w-3" />}
                            {kind.label}
                          </Badge>
                        </TableCell>
                        <TableCell className="font-mono text-xs">{m.doc}</TableCell>
                        <TableCell className="hidden text-xs text-muted-foreground sm:table-cell">{m.actor}</TableCell>
                        <TableCell
                          className={cn(
                            'text-right font-semibold tabular-nums',
                            m.kind === 'in' && 'text-emerald-600 dark:text-emerald-400',
                            m.kind === 'out' && 'text-orange-600 dark:text-orange-400',
                            m.kind === 'writeoff' && 'text-red-600 dark:text-red-400'
                          )}
                        >
                          {m.kind === 'in' ? '+' : m.kind === 'out' ? '−' : m.kind === 'writeoff' ? '×' : '→'} {m.qty}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </div>

            <DialogFooter className="gap-2 sm:gap-0">
              <div className="flex w-full flex-col gap-2 sm:flex-row sm:justify-between">
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="gap-1.5"
                    onClick={() => showToast('Приёмка открыта', `${item.name} · форма приходной накладной`)}
                  >
                    <ArrowDownToLine className="h-3.5 w-3.5" /> Принять приход
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="gap-1.5 text-red-600 hover:bg-red-50 hover:text-red-700 dark:hover:bg-red-950/40 dark:hover:text-red-400"
                    onClick={() => showToast('Акт списания', `Черновик акта для ${item.sku} создан`)}
                  >
                    <ArrowUpFromLine className="h-3.5 w-3.5" /> Списать
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="gap-1.5"
                    onClick={() => showToast('Ярлык отправлен на печать', `${item.sku} · ячейка ${d.shelf}`)}
                  >
                    <Printer className="h-3.5 w-3.5" /> Ярлык
                  </Button>
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
  )
}

'use client'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ViewHeader, StockLevelBadge, stockLevelOf, DraftNote } from './shared'
import { ItemDialog } from './item-dialog'
import { stockData, warehouseStats, fmtMoney, fmtNum, type StockRow } from '@/lib/erp-data'
import {
  Plus,
  Search,
  PackageSearch,
  Warehouse,
  ArrowDownUp,
  QrCode,
  TruckIcon,
  AlertTriangle,
  Boxes,
  TrendingDown,
  TrendingUp,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { cn } from '@/lib/utils'

export function InventoryView() {
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('all')
  const [selectedItem, setSelectedItem] = useState<StockRow | null>(null)

  const categories = useMemo(() => Array.from(new Set(stockData.map((s) => s.category))), [])

  const filtered = useMemo(
    () =>
      stockData.filter(
        (s) =>
          (category === 'all' || s.category === category) &&
          (s.name.toLowerCase().includes(query.toLowerCase()) ||
            s.sku.toLowerCase().includes(query.toLowerCase()))
      ),
    [query, category]
  )

  const criticalCount = stockData.filter(
    (s) => ['low', 'critical'].includes(stockLevelOf(s.qty, s.minQty, s.capacity))
  ).length
  const totalValue = stockData.reduce((sum, s) => sum + s.qty * s.price, 0)

  return (
    <div className="space-y-6">
      <ViewHeader title="Склад" subtitle="Остатки, резервы и движение товаров">
        <Button variant="outline" size="sm" className="gap-1.5">
          <QrCode className="h-4 w-4" />
          <span className="hidden sm:inline">Скан-код</span>
        </Button>
        <Button variant="outline" size="sm" className="gap-1.5">
          <ArrowDownUp className="h-4 w-4" />
          <span className="hidden sm:inline">Перемещение</span>
        </Button>
        <Button size="sm" className="gap-1.5 bg-emerald-600 text-white hover:bg-emerald-700">
          <Plus className="h-4 w-4" /> Приёмка
        </Button>
      </ViewHeader>

      {/* Мини-карточки */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
          <CardContent className="flex items-center gap-4 p-5">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-emerald-50 text-emerald-600 dark:bg-emerald-950/60 dark:text-emerald-400">
              <Boxes className="h-5 w-5" />
            </span>
            <div>
              <p className="text-sm text-muted-foreground">Стоимость запасов</p>
              <p className="text-lg font-bold">{fmtMoney(totalValue)}</p>
            </div>
          </CardContent>
        </Card>
        <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
          <CardContent className="flex items-center gap-4 p-5">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-red-50 text-red-600 dark:bg-red-950/60 dark:text-red-400">
              <AlertTriangle className="h-5 w-5" />
            </span>
            <div>
              <p className="text-sm text-muted-foreground">Позиций с низким остатком</p>
              <p className="text-lg font-bold text-red-600">{criticalCount}</p>
            </div>
          </CardContent>
        </Card>
        <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
          <CardContent className="flex items-center gap-4 p-5">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-teal-50 text-teal-600 dark:bg-teal-950/60 dark:text-teal-400">
              <TruckIcon className="h-5 w-5" />
            </span>
            <div>
              <p className="text-sm text-muted-foreground">Ожидают приёмки</p>
              <p className="text-lg font-bold">7 поставок</p>
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-4">
        {/* Таблица остатков */}
        <Card className="border-zinc-200 shadow-sm dark:border-zinc-800 xl:col-span-3">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Номенклатура</CardTitle>
            <CardDescription>{filtered.length} позиций по всем складам</CardDescription>
            <div className="mt-2 flex flex-col gap-2 sm:flex-row">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Артикул или название…"
                  className="pl-9"
                />
              </div>
              <Select value={category} onValueChange={setCategory}>
                <SelectTrigger className="w-full sm:w-[200px]">
                  <SelectValue placeholder="Все категории" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Все категории</SelectItem>
                  {categories.map((c) => (
                    <SelectItem key={c} value={c}>{c}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="bg-zinc-50/80 hover:bg-zinc-50/80 dark:bg-zinc-900/50">
                    <TableHead>Артикул</TableHead>
                    <TableHead>Наименование</TableHead>
                    <TableHead className="hidden lg:table-cell">Склад</TableHead>
                    <TableHead className="text-right">Остаток</TableHead>
                    <TableHead className="hidden sm:table-cell w-[140px]">Заполненность</TableHead>
                    <TableHead className="hidden md:table-cell text-right">Цена</TableHead>
                    <TableHead>Состояние</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filtered.map((s) => {
                    const level = stockLevelOf(s.qty, s.minQty, s.capacity)
                    const pct = Math.min(100, Math.round((s.qty / s.capacity) * 100))
                    return (
                      <TableRow
                        key={s.sku}
                        className="group cursor-pointer"
                        onClick={() => setSelectedItem(s)}
                      >
                        <TableCell className="font-mono text-xs text-muted-foreground transition-colors group-hover:text-emerald-700 dark:group-hover:text-emerald-400">{s.sku}</TableCell>
                        <TableCell>
                          <div className="text-sm font-medium transition-colors group-hover:underline">{s.name}</div>
                          <div className="text-xs text-muted-foreground">{s.category}</div>
                        </TableCell>
                        <TableCell className="hidden lg:table-cell text-sm text-muted-foreground whitespace-nowrap">{s.warehouse}</TableCell>
                        <TableCell className="text-right">
                          <span className={cn('font-semibold', level === 'critical' && 'text-red-600', level === 'low' && 'text-amber-600')}>
                            {s.qty >= 999 ? '∞' : fmtNum(s.qty)}
                          </span>
                          <span className="text-xs text-muted-foreground"> / мин. {s.minQty}</span>
                        </TableCell>
                        <TableCell className="hidden sm:table-cell">
                          <Progress
                            value={pct}
                            className={cn(
                              'h-2 [&>div]:bg-emerald-500',
                              level === 'critical' && '[&>div]:bg-red-500',
                              level === 'low' && '[&>div]:bg-amber-500'
                            )}
                          />
                          <span className="mt-1 block text-[11px] text-muted-foreground">{pct}% ёмкости</span>
                        </TableCell>
                        <TableCell className="hidden md:table-cell text-right text-sm whitespace-nowrap">{fmtMoney(s.price)}</TableCell>
                        <TableCell><StockLevelBadge level={level} /></TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>

        {/* Правая колонка: склады + движение */}
        <div className="space-y-4">
          <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
            <CardHeader className="flex-row items-center gap-2 space-y-0 pb-3">
              <Warehouse className="h-4 w-4 text-emerald-600" />
              <CardTitle className="text-base">Склады</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {warehouseStats.map((w) => (
                <div key={w.name}>
                  <div className="mb-1.5 flex items-center justify-between text-sm">
                    <span className="font-medium">{w.name}</span>
                    <span className={cn('text-xs font-semibold', w.fill > 80 ? 'text-red-600' : 'text-muted-foreground')}>{w.fill}%</span>
                  </div>
                  <Progress value={w.fill} className={cn('h-2 [&>div]:bg-emerald-500', w.fill > 80 && '[&>div]:bg-red-500')} />
                </div>
              ))}
            </CardContent>
          </Card>

          <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
            <CardHeader className="flex-row items-center gap-2 space-y-0 pb-3">
              <PackageSearch className="h-4 w-4 text-emerald-600" />
              <CardTitle className="text-base">Движения за сутки</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="flex items-center justify-between rounded-lg bg-emerald-50 px-3 py-2 dark:bg-emerald-950/40">
                <span className="flex items-center gap-2 text-emerald-800"><TrendingUp className="h-4 w-4" /> Приход</span>
                <span className="font-bold text-emerald-700">+128 поз.</span>
              </div>
              <div className="flex items-center justify-between rounded-lg bg-orange-50 px-3 py-2 dark:bg-orange-950/40">
                <span className="flex items-center gap-2 text-orange-800"><TrendingDown className="h-4 w-4" /> Расход</span>
                <span className="font-bold text-orange-700">−96 поз.</span>
              </div>
              <div className="flex items-center justify-between rounded-lg bg-zinc-50 px-3 py-2 dark:bg-zinc-800/50">
                <span className="flex items-center gap-2 text-zinc-700"><ArrowDownUp className="h-4 w-4" /> Перемещения</span>
                <span className="font-bold text-zinc-700">11 актов</span>
              </div>
              <div className="flex items-center justify-between rounded-lg bg-amber-50 px-3 py-2 dark:bg-amber-950/40">
                <span className="flex items-center gap-2 text-amber-800"><AlertTriangle className="h-4 w-4" /> Списания</span>
                <span className="font-bold text-amber-700">2 позиции</span>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      <DraftNote text="Макет: клик по строке открывает карточку товара. Здесь появятся инвентаризация и правила резервирования." />

      {/* Карточка товара */}
      <ItemDialog item={selectedItem} onClose={() => setSelectedItem(null)} />
    </div>
  )
}

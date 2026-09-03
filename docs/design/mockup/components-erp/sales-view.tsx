'use client'

import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Checkbox } from '@/components/ui/checkbox'
import { AnimatePresence, motion } from 'framer-motion'
import { SalesFunnel } from './sales-funnel'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ViewHeader, OrderStatusBadge, DraftNote, orderStatusMap } from './shared'
import { OrderDialog } from './order-dialog'
import { ordersData, fmtMoney, downloadCsv, type OrderStatus, type OrderRow } from '@/lib/erp-data'
import { useToast } from '@/hooks/use-toast'
import {
  Plus,
  Search,
  Filter,
  Download,
  MoreHorizontal,
  Eye,
  Pencil,
  Copy,
  Trash2,
  ChevronLeft,
  ChevronRight,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  KanbanSquare,
  ReceiptText,
  Check,
  Repeat,
  X,
  TableProperties,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { cn } from '@/lib/utils'

const avatarTones = ['bg-emerald-100 text-emerald-700', 'bg-teal-100 text-teal-700', 'bg-amber-100 text-amber-700', 'bg-orange-100 text-orange-700']

const channelsMap: Record<string, string> = {
  'Онлайн': 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-900',
  'Телефон': 'border-teal-200 bg-teal-50 text-teal-700 dark:bg-teal-950/60 dark:text-teal-300 dark:border-teal-900',
  'Партнёр': 'border-amber-200 bg-amber-50 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300 dark:border-amber-900',
  'Тендер': 'border-orange-200 bg-orange-50 text-orange-700 dark:bg-orange-950/60 dark:text-orange-300 dark:border-orange-900',
}

// ранг статуса для сортировки по жизненному циклу, а не по алфавиту
const statusRank: Record<OrderStatus, number> = {
  new: 0,
  processing: 1,
  shipping: 2,
  done: 3,
  cancelled: 4,
}

type SortKey = 'id' | 'customer' | 'date' | 'amount' | 'status'
type SortDir = 'asc' | 'desc'

function SortableHead({
  label,
  sortKey,
  active,
  dir,
  onSort,
  className,
}: {
  label: string
  sortKey: SortKey
  active: boolean
  dir: SortDir
  onSort: (k: SortKey) => void
  className?: string
}) {
  const Icon = !active ? ArrowUpDown : dir === 'asc' ? ArrowUp : ArrowDown
  return (
    <TableHead className={className}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        aria-label={`Сортировать по полю «${label}»`}
        className={cn(
          'inline-flex items-center gap-1 font-medium transition-colors hover:text-foreground',
          active && 'text-emerald-700 dark:text-emerald-400'
        )}
      >
        {label}
        <Icon className={cn('h-3 w-3 transition-opacity', active ? 'opacity-100' : 'opacity-50')} />
      </button>
    </TableHead>
  )
}

const allStatuses: OrderStatus[] = ['new', 'processing', 'shipping', 'done', 'cancelled']

export function SalesView() {
  const { toast } = useToast()
  const [tab, setTab] = useState<'orders' | 'funnel'>('orders')
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<'all' | OrderStatus>('all')
  // Живой список заказов: инлайн-смена статуса меняет и таблицу, и карточку
  const [orders, setOrders] = useState<OrderRow[]>(ordersData)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selectedOrder = orders.find((o) => o.id === selectedId) ?? null
  const [sortKey, setSortKey] = useState<SortKey | null>(null)
  const [sortDir, setSortDir] = useState<SortDir>('asc')

  const changeStatus = (o: OrderRow, next: OrderStatus) => {
    if (next === o.status) return
    setOrders((prev) => prev.map((x) => (x.id === o.id ? { ...x, status: next } : x)))
    const st = orderStatusMap[next]
    toast({
      title: `Статус заказа ${o.id} обновлён`,
      description: `${orderStatusMap[o.status].label} → ${st.label} · уведомили клиента и менеджера`,
      duration: 3000,
    })
  }

  // ---------- Массовые действия (чекбоксы) ----------
  // (всё, что зависит от sorted, объявляется после него — см. ниже)
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const toggleOne = (id: string) => {
    setChecked((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  const clearChecked = () => setChecked(new Set())

  const onSort = (k: SortKey) => {
    if (sortKey === k) {
      if (sortDir === 'asc') setSortDir('desc')
      else {
        setSortKey(null) // третий клик — сброс сортировки
        setSortDir('asc')
      }
    } else {
      setSortKey(k)
      setSortDir('asc')
    }
  }

  const filtered = useMemo(
    () =>
      orders.filter(
        (o) =>
          (status === 'all' || o.status === status) &&
          (o.id.toLowerCase().includes(query.toLowerCase()) ||
            o.customer.toLowerCase().includes(query.toLowerCase()) ||
            o.manager.toLowerCase().includes(query.toLowerCase()))
      ),
    [orders, query, status]
  )

  const sorted = useMemo(() => {
    if (!sortKey) return filtered
    const copy = [...filtered]
    copy.sort((a, b) => {
      let cmp = 0
      switch (sortKey) {
        case 'id':
        case 'customer':
          cmp = a[sortKey].localeCompare(b[sortKey], 'ru')
          break
        case 'date':
          cmp = a.date.split('.').reverse().join('').localeCompare(b.date.split('.').reverse().join(''))
          break
        case 'amount':
          cmp = a.amount - b.amount
          break
        case 'status':
          cmp = statusRank[a.status] - statusRank[b.status]
          break
      }
      return sortDir === 'asc' ? cmp : -cmp
    })
    return copy
  }, [filtered, sortKey, sortDir])

  const totalAmount = sorted.reduce((s, o) => s + o.amount, 0)

  // Массовые действия: вычисления после sorted (чтобы не попасть в TDZ)
  const allChecked = sorted.length > 0 && sorted.every((o) => checked.has(o.id))
  const someChecked = checked.size > 0 && !allChecked
  const toggleAll = () => {
    setChecked(allChecked ? new Set() : new Set(sorted.map((o) => o.id)))
  }
  const checkedOrders = orders.filter((o) => checked.has(o.id))
  const checkedSum = checkedOrders.reduce((s, o) => s + o.amount, 0)

  const bulkStatus = (next: OrderStatus) => {
    let changed = 0
    setOrders((prev) =>
      prev.map((x) => {
        if (!checked.has(x.id) || x.status === next) return x
        changed += 1
        return { ...x, status: next }
      })
    )
    toast({
      title: `Статусы обновлены: ${changed}`,
      description: `Заказы переведены в «${orderStatusMap[next].label}» · клиенты и менеджеры уведомлены`,
      duration: 3000,
    })
  }

  const bulkDelete = () => {
    const ids = [...checked]
    setOrders((prev) => prev.filter((o) => !checked.has(o.id)))
    if (selectedId && checked.has(selectedId)) setSelectedId(null)
    clearChecked()
    toast({
      title: `Заказы удалены: ${ids.length}`,
      description: `${ids.slice(0, 3).join(', ')}${ids.length > 3 ? '…' : ''} · восстановление из архива`,
      duration: 3000,
    })
  }

  const exportSelected = () => {
    downloadCsv(
      `zakazy-${checkedOrders.length}.csv`,
      checkedOrders.map((o) => ({
        number: o.id,
        client: o.customer,
        manager: o.manager,
        channel: o.channel,
        date: o.date,
        amount: o.amount,
        items: o.items,
        status: orderStatusMap[o.status].label,
      })),
      [
        { key: 'number', label: 'Номер' },
        { key: 'client', label: 'Клиент' },
        { key: 'manager', label: 'Менеджер' },
        { key: 'channel', label: 'Канал' },
        { key: 'date', label: 'Дата' },
        { key: 'amount', label: 'Сумма, ₽' },
        { key: 'items', label: 'Позиций' },
        { key: 'status', label: 'Статус' },
      ]
    )
    toast({
      title: 'CSV сформирован',
      description: `${checkedOrders.length} заказов · файл в загрузках (Excel-совместимый)`,
      duration: 3000,
    })
  }

  return (
    <div className="space-y-6">
      <ViewHeader title="Продажи" subtitle="Заказы клиентов · декабрь 2025">
        <Button variant="outline" size="sm" className="gap-1.5">
          <Download className="h-4 w-4" />
          <span className="hidden sm:inline">Экспорт</span>
        </Button>
        <Button size="sm" className="gap-1.5 bg-emerald-600 text-white hover:bg-emerald-700">
          <Plus className="h-4 w-4" /> {tab === 'funnel' ? 'Новая сделка' : 'Новый заказ'}
        </Button>
      </ViewHeader>

      <Tabs value={tab} onValueChange={(v) => setTab(v as 'orders' | 'funnel')} className="space-y-5">
        <TabsList className="h-9 bg-zinc-100 p-1 dark:bg-zinc-900">
          <TabsTrigger
            value="orders"
            className="gap-1.5 rounded-md px-3 text-xs data-[state=active]:bg-white data-[state=active]:text-emerald-700 data-[state=active]:shadow-sm dark:data-[state=active]:bg-zinc-800 dark:data-[state=active]:text-emerald-400 sm:text-[13px]"
          >
            <ReceiptText className="h-3.5 w-3.5" /> Заказы
          </TabsTrigger>
          <TabsTrigger
            value="funnel"
            className="gap-1.5 rounded-md px-3 text-xs data-[state=active]:bg-white data-[state=active]:text-emerald-700 data-[state=active]:shadow-sm dark:data-[state=active]:bg-zinc-800 dark:data-[state=active]:text-emerald-400 sm:text-[13px]"
          >
            <KanbanSquare className="h-3.5 w-3.5" /> Воронка сделок
          </TabsTrigger>
        </TabsList>

        <TabsContent value="orders" className="mt-0 space-y-6">

      {/* Панель фильтров */}
      <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Поиск по номеру, клиенту или менеджеру…"
              className="pl-9"
            />
          </div>
          <Select value={status} onValueChange={(v) => setStatus(v as 'all' | OrderStatus)}>
            <SelectTrigger className="w-full sm:w-[180px]">
              <SelectValue placeholder="Все статусы" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Все статусы</SelectItem>
              <SelectItem value="new">Новые</SelectItem>
              <SelectItem value="processing">В работе</SelectItem>
              <SelectItem value="shipping">Отгрузка</SelectItem>
              <SelectItem value="done">Выполнены</SelectItem>
              <SelectItem value="cancelled">Отменены</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="outline" className="gap-1.5">
            <Filter className="h-4 w-4" /> Ещё фильтры
          </Button>
        </CardContent>
      </Card>

      {/* Сводка по фильтру */}
      <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <span>
          Найдено: <span className="font-semibold text-foreground">{sorted.length}</span> заказов на сумму{' '}
          <span className="font-semibold text-foreground">{fmtMoney(totalAmount)}</span>
          {sortKey && (
            <>
              {' '}· сортировка:{' '}
              <span className="font-medium text-emerald-700 dark:text-emerald-400">
                {{ id: 'номер', customer: 'клиент', date: 'дата', amount: 'сумма', status: 'статус' }[sortKey]}
                {' '}{sortDir === 'asc' ? '↑' : '↓'}
              </span>
            </>
          )}
        </span>
      </div>

      {/* Таблица заказов */}
      <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow className="bg-zinc-50/80 hover:bg-zinc-50/80 dark:bg-zinc-900/50">
                  <TableHead className="w-10">
                    <Checkbox
                      checked={allChecked ? true : someChecked ? 'indeterminate' : false}
                      onCheckedChange={toggleAll}
                      aria-label="Выбрать все заказы на странице"
                      className="data-[state=checked]:border-emerald-600 data-[state=checked]:bg-emerald-600 data-[state=indeterminate]:border-emerald-600 data-[state=indeterminate]:bg-emerald-600"
                    />
                  </TableHead>
                  <SortableHead label="Номер" sortKey="id" active={sortKey === 'id'} dir={sortDir} onSort={onSort} className="w-[110px]" />
                  <SortableHead label="Клиент" sortKey="customer" active={sortKey === 'customer'} dir={sortDir} onSort={onSort} />
                  <TableHead className="hidden md:table-cell">Менеджер</TableHead>
                  <TableHead className="hidden lg:table-cell">Канал</TableHead>
                  <SortableHead label="Дата" sortKey="date" active={sortKey === 'date'} dir={sortDir} onSort={onSort} className="hidden sm:table-cell" />
                  <SortableHead label="Сумма" sortKey="amount" active={sortKey === 'amount'} dir={sortDir} onSort={onSort} className="text-right" />
                  <TableHead className="hidden sm:table-cell text-center">Позиций</TableHead>
                  <SortableHead label="Статус" sortKey="status" active={sortKey === 'status'} dir={sortDir} onSort={onSort} />
                  <TableHead className="w-[50px]" />
                </TableRow>
              </TableHeader>
              <TableBody key={`${sortKey ?? 'default'}-${sortDir}`}>
                {sorted.map((o, i) => (
                  <TableRow
                    key={o.id}
                    className={cn(
                      'group cursor-pointer erp-row-in transition-colors',
                      checked.has(o.id) && 'bg-emerald-50/60 dark:bg-emerald-950/20'
                    )}
                    style={{ animationDelay: `${Math.min(i * 22, 260)}ms` }}
                    onClick={() => setSelectedId(o.id)}
                  >
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      <Checkbox
                        checked={checked.has(o.id)}
                        onCheckedChange={() => toggleOne(o.id)}
                        aria-label={`Выбрать заказ ${o.id}`}
                        className="data-[state=checked]:border-emerald-600 data-[state=checked]:bg-emerald-600"
                      />
                    </TableCell>
                    <TableCell className="font-medium text-emerald-700 dark:text-emerald-400">{o.id}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <Avatar className="h-7 w-7">
                          <AvatarFallback className={avatarTones[i % avatarTones.length]}>
                            {o.customer.replace(/[^А-Я]/g, '').slice(0, 2)}
                          </AvatarFallback>
                        </Avatar>
                        <span className="whitespace-nowrap text-sm group-hover:underline">{o.customer}</span>
                      </div>
                    </TableCell>
                    <TableCell className="hidden md:table-cell text-sm text-muted-foreground">{o.manager}</TableCell>
                    <TableCell className="hidden lg:table-cell">
                      <Badge variant="outline" className={channelsMap[o.channel]}>{o.channel}</Badge>
                    </TableCell>
                    <TableCell className="hidden sm:table-cell text-sm text-muted-foreground whitespace-nowrap">{o.date}</TableCell>
                    <TableCell className="text-right font-semibold whitespace-nowrap">{fmtMoney(o.amount)}</TableCell>
                    <TableCell className="hidden sm:table-cell text-center text-sm text-muted-foreground">{o.items}</TableCell>
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      {/* Инлайн-смена статуса: клик по бейджу открывает меню */}
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <button
                            type="button"
                            title="Изменить статус"
                            aria-label={`Изменить статус заказа ${o.id}, сейчас: ${orderStatusMap[o.status].label}`}
                            className="rounded-md transition-transform hover:scale-[1.04] active:scale-95 focus-visible:outline-2 focus-visible:outline-emerald-600"
                          >
                            <OrderStatusBadge status={o.status} />
                          </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="start" className="w-48">
                          <p className="flex items-center gap-1.5 px-2 py-1.5 text-[11px] font-medium text-muted-foreground">
                            <Repeat className="h-3 w-3" /> Сменить статус
                          </p>
                          {allStatuses.map((s) => {
                            const sm = orderStatusMap[s]
                            const current = s === o.status
                            return (
                              <DropdownMenuItem
                                key={s}
                                disabled={current}
                                onClick={() => changeStatus(o, s)}
                                className={cn(current && 'bg-emerald-50/60 dark:bg-emerald-950/30')}
                              >
                                <span className="mr-1.5 flex h-3.5 w-3.5 items-center justify-center">{sm.icon}</span> {sm.label}
                                {current && <Check className="ml-auto h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />}
                              </DropdownMenuItem>
                            )
                          })}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                    <TableCell>
                      <div onClick={(e) => e.stopPropagation()}>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" className="h-8 w-8 opacity-0 transition-opacity group-hover:opacity-100 data-[state=open]:opacity-100">
                              <MoreHorizontal className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={() => setSelectedId(o.id)}><Eye className="mr-2 h-4 w-4" /> Открыть</DropdownMenuItem>
                            <DropdownMenuItem><Pencil className="mr-2 h-4 w-4" /> Редактировать</DropdownMenuItem>
                            <DropdownMenuItem><Copy className="mr-2 h-4 w-4" /> Дублировать</DropdownMenuItem>
                            <DropdownMenuItem className="text-red-600 focus:text-red-600">
                              <Trash2 className="mr-2 h-4 w-4" /> Удалить
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
                {sorted.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={10} className="py-10 text-center text-sm text-muted-foreground">
                      Заказы не найдены — измените параметры поиска
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>

          {/* Пагинация */}
          <div className="flex items-center justify-between border-t border-zinc-100 px-4 py-3 dark:border-zinc-800">
            <span className="text-xs text-muted-foreground">Страница 1 из 4 · показано {sorted.length} из 46</span>
            <div className="flex items-center gap-1">
              <Button variant="outline" size="icon" className="h-8 w-8" disabled>
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <Button variant="outline" size="icon" className="h-8 w-8 bg-emerald-600 text-white hover:bg-emerald-700 hover:text-white">
                1
              </Button>
              <Button variant="outline" size="icon" className="h-8 w-8">2</Button>
              <Button variant="outline" size="icon" className="h-8 w-8">3</Button>
              <Button variant="outline" size="icon" className="h-8 w-8">
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <DraftNote text="Макет: клик по строке открывает карточку заказа. Здесь будут документ отгрузки и резервирование товара на складе." />

      {/* Плавающая панель массовых действий */}
      <AnimatePresence>
        {checked.size > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 24, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 24, scale: 0.96 }}
            transition={{ duration: 0.18, ease: 'easeOut' }}
            className="fixed inset-x-3 bottom-4 z-40 sm:inset-x-auto sm:left-1/2 sm:bottom-6 sm:-translate-x-1/2"
          >
            <div className="flex flex-wrap items-center gap-2 rounded-xl border border-zinc-200 bg-background/95 p-2 shadow-xl backdrop-blur dark:border-zinc-700 sm:gap-3 sm:px-3">
              <span className="flex items-center gap-1.5 pl-1 text-sm font-medium">
                <TableProperties className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                Выбрано: <span className="font-bold text-emerald-700 dark:text-emerald-400">{checked.size}</span>
                <span className="hidden text-xs font-normal text-muted-foreground md:inline">
                  · на {fmtMoney(checkedSum)}
                </span>
              </span>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="sm" className="h-8 gap-1.5">
                    <Repeat className="h-3.5 w-3.5" /> Статус
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-44">
                  {allStatuses.map((s) => {
                    const sm = orderStatusMap[s]
                    return (
                      <DropdownMenuItem key={s} onClick={() => bulkStatus(s)}>
                        <span className="mr-1.5 flex h-3.5 w-3.5 items-center justify-center">{sm.icon}</span>
                        {sm.label}
                      </DropdownMenuItem>
                    )
                  })}
                </DropdownMenuContent>
              </DropdownMenu>
              <Button variant="outline" size="sm" className="h-8 gap-1.5" onClick={exportSelected}>
                <Download className="h-3.5 w-3.5" /> CSV
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="h-8 gap-1.5 text-red-600 hover:bg-red-50 hover:text-red-700 dark:text-red-400 dark:hover:bg-red-950/40 dark:hover:text-red-300"
                onClick={bulkDelete}
              >
                <Trash2 className="h-3.5 w-3.5" /> Удалить
              </Button>
              <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground" onClick={clearChecked} aria-label="Снять выделение">
                <X className="h-4 w-4" />
              </Button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Карточка заказа (получает живой статус из списка) */}
      <OrderDialog order={selectedOrder} onClose={() => setSelectedId(null)} />
        </TabsContent>

        <TabsContent value="funnel" className="mt-0 space-y-6">
          <SalesFunnel />
          <DraftNote text="Макет: перетащите сделку в следующий этап — статусы менеджеров и прогноз обновятся. Здесь появятся автозадачи и напоминания." />
        </TabsContent>
      </Tabs>
    </div>
  )
}

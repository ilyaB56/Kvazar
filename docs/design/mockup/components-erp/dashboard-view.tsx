'use client'

import { useEffect, useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { DeltaPill } from './shared'
import { TaskKanban } from './task-kanban'
import {
  kpiData,
  revenueByMonth,
  salesByCategory,
  activityData,
  warehouseStats,
  fmtMoney,
  overdueInvoices,
  dueSoonInvoices,
  kpiSparklines,
  DEMO_TODAY,
  daysBetween,
  parseRuDate,
  pluralRu,
} from '@/lib/erp-data'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  Banknote,
  ClipboardList,
  Users,
  Warehouse,
  ArrowUpRight,
  Download,
  Package,
  ShoppingCart,
  Landmark,
  UserRound,
  Cog,
  MoreHorizontal,
  TrendingUp,
  AlertOctagon,
  CalendarClock,
  BellRing,
  BadgeCheck,
} from 'lucide-react'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'

const kpiIcons = {
  revenue: <Banknote className="h-4 w-4" />,
  orders: <ClipboardList className="h-4 w-4" />,
  clients: <Users className="h-4 w-4" />,
  warehouse: <Warehouse className="h-4 w-4" />,
}

const activityIcons = {
  order: <ShoppingCart className="h-3.5 w-3.5" />,
  stock: <Package className="h-3.5 w-3.5" />,
  finance: <Landmark className="h-3.5 w-3.5" />,
  hr: <UserRound className="h-3.5 w-3.5" />,
  system: <Cog className="h-3.5 w-3.5" />,
}

const activityTones: Record<string, string> = {
  order: 'bg-emerald-100 text-emerald-700',
  stock: 'bg-teal-100 text-teal-700',
  finance: 'bg-amber-100 text-amber-700',
  hr: 'bg-sky-100 text-sky-700',
  system: 'bg-red-100 text-red-600',
}

const pieColors = ['#059669', '#0d9488', '#d97706', '#ea580c', '#65a30d']

// ---------- Count-up для цифр KPI ----------
// Анимация от 0 до значения с ease-out cubic; при prefers-reduced-motion — мгновенно
function useCountUp(target: number, duration = 1100): number {
  const [val, setVal] = useState(0)
  useEffect(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let raf = 0
    const start = performance.now()
    const tick = (now: number) => {
      const p = reduced ? 1 : Math.min(1, (now - start) / duration)
      const eased = 1 - Math.pow(1 - p, 3)
      setVal(target * eased)
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, duration])
  return val
}

function CountUpValue({ value, decimals, suffix }: { value: number; decimals: number; suffix: string }) {
  const current = useCountUp(value)
  return (
    <span className="text-2xl font-bold tracking-tight tabular-nums">
      {current.toLocaleString('ru-RU', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })}
      {suffix}
    </span>
  )
}

// Мини-график тренда внутри KPI-карточки (12 месяцев)
function KpiSparkline({ points, negative }: { points: number[]; negative?: boolean }) {
  const data = points.map((v, i) => ({ i, v }))
  const stroke = negative ? '#ef4444' : '#10b981'
  const gid = negative ? 'sparkRed' : 'sparkGreen'
  return (
    <div className="mt-2 h-9" aria-hidden>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity={0.3} />
              <stop offset="100%" stopColor={stroke} stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area
            type="monotone"
            dataKey="v"
            stroke={stroke}
            strokeWidth={1.8}
            fill={`url(#${gid})`}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

export function DashboardView({ onOpenFinance }: { onOpenFinance?: () => void }) {
  const { toast } = useToast()

  // Дебиторка собирается из живых данных макета: просрочка + платежи ближайших 7 дней
  const overdue = overdueInvoices()
  const dueSoon = dueSoonInvoices(7)
  const overdueSum = overdue.reduce((s, i) => s + i.amount, 0)
  const dueSoonSum = dueSoon.reduce((s, x) => s + x.inv.amount, 0)
  const receivableOk = overdue.length === 0 && dueSoon.length === 0

  return (
    <div className="space-y-6">
      {/* KPI карточки */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {kpiData.map((k) => (
          <Card key={k.id} className="group border-zinc-200 shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md dark:border-zinc-800">
            <CardContent className="p-5">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">{k.label}</span>
                <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600 transition-transform duration-200 group-hover:scale-110 dark:bg-emerald-950/60 dark:text-emerald-400">
                  {kpiIcons[k.icon]}
                </span>
              </div>
              <div className="mt-3 flex items-baseline gap-2">
                <CountUpValue value={k.numValue} decimals={k.decimals} suffix={k.suffix} />
                <DeltaPill delta={k.delta} invert={k.id === 'k4'} />
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{k.hint}</p>
              {kpiSparklines[k.id] && <KpiSparkline points={kpiSparklines[k.id]} />}
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Виджет дебиторки: просрочка + ближайшие платежи (живые данные) */}
      <Card
        className={cn(
          'overflow-hidden border shadow-sm',
          receivableOk
            ? 'border-emerald-200 dark:border-emerald-900/60'
            : overdue.length > 0
              ? 'border-red-200 dark:border-red-900/60'
              : 'border-amber-200 dark:border-amber-900/60'
        )}
      >
        <div
          className={cn(
            'flex flex-col gap-4 p-4 sm:p-5',
            receivableOk
              ? 'bg-emerald-50/50 dark:bg-emerald-950/20'
              : overdue.length > 0
                ? 'bg-red-50/50 dark:bg-red-950/20'
                : 'bg-amber-50/50 dark:bg-amber-950/20'
          )}
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              {receivableOk ? (
                <BadgeCheck className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
              ) : (
                <AlertOctagon className={cn('h-5 w-5', overdue.length > 0 ? 'text-red-600 dark:text-red-400' : 'text-amber-600 dark:text-amber-400')} />
              )}
              <p className="text-sm font-semibold">Дебиторка требует внимания</p>
              {overdue.length > 0 && (
                <Badge className="border-transparent bg-red-500 text-[10px] text-white hover:bg-red-500">
                  {overdue.length} {pluralRu(overdue.length, 'просрочен', 'просрочено', 'просрочено')}
                </Badge>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                size="sm"
                className="h-8 gap-1.5 bg-white/70 dark:bg-zinc-900/70"
                onClick={() =>
                  toast({
                    title: 'Напоминания отправлены',
                    description: `${overdue.length + dueSoon.length} ${pluralRu(overdue.length + dueSoon.length, 'клиент уведомлён', 'клиента уведомлено', 'клиентов уведомлено')} · письма в очереди`,
                    duration: 3000,
                  })
                }
              >
                <BellRing className="h-3.5 w-3.5" /> Напомнить клиентам
              </Button>
              <Button
                size="sm"
                className="h-8 gap-1.5 bg-emerald-600 text-white hover:bg-emerald-700"
                onClick={() => onOpenFinance?.()}
              >
                Открыть финансы <ArrowUpRight className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>

          {receivableOk ? (
            <p className="text-sm text-emerald-700 dark:text-emerald-300">
              Все счета оплачены в срок — просроченной задолженности нет.
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {/* Просроченные */}
              <div className="space-y-2">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-red-700 dark:text-red-400">
                  Просрочено · {fmtMoney(overdueSum)}
                </p>
                {overdue.length === 0 && <p className="text-xs text-muted-foreground">Просрочек нет</p>}
                {overdue.map((inv) => {
                  const days = Math.max(0, daysBetween(DEMO_TODAY, parseRuDate(inv.dueDate)))
                  return (
                    <button
                      key={inv.id}
                      onClick={() => onOpenFinance?.()}
                      className="flex w-full items-center justify-between gap-2 rounded-lg border border-red-200 bg-white px-3 py-2 text-left transition-all hover:-translate-y-px hover:shadow-sm dark:border-red-900/60 dark:bg-zinc-900/70"
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-[13px] font-medium">{inv.counterparty}</span>
                        <span className="block text-[11px] text-muted-foreground">
                          {inv.id} · просрочка {days} {pluralRu(days, 'день', 'дня', 'дней')}
                        </span>
                      </span>
                      <span className="shrink-0 text-[13px] font-semibold text-red-700 dark:text-red-400">
                        {fmtMoney(inv.amount)}
                      </span>
                    </button>
                  )
                })}
              </div>

              {/* Ближайшие платежи */}
              <div className="space-y-2">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-400">
                  Ближайшие 7 дней · {fmtMoney(dueSoonSum)}
                </p>
                {dueSoon.length === 0 && <p className="text-xs text-muted-foreground">Платежей не запланировано</p>}
                {dueSoon.map(({ inv, daysLeft }) => (
                  <button
                    key={inv.id}
                    onClick={() => onOpenFinance?.()}
                    className="flex w-full items-center justify-between gap-2 rounded-lg border border-amber-200 bg-white px-3 py-2 text-left transition-all hover:-translate-y-px hover:shadow-sm dark:border-amber-900/60 dark:bg-zinc-900/70"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-[13px] font-medium">{inv.counterparty}</span>
                      <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
                        <CalendarClock className="h-3 w-3" />
                        {inv.id} · до {inv.dueDate} (через {daysLeft} {pluralRu(daysLeft, 'день', 'дня', 'дней')})
                      </span>
                    </span>
                    <span className="shrink-0 text-[13px] font-semibold text-amber-700 dark:text-amber-400">
                      {fmtMoney(inv.amount)}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </Card>

      {/* Графики */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="border-zinc-200 shadow-sm dark:border-zinc-800 xl:col-span-2">
          <CardHeader className="flex-row items-start justify-between space-y-0 pb-2">
            <div>
              <CardTitle className="text-base">Выручка и прибыль</CardTitle>
              <CardDescription>Динамика за 12 месяцев, тыс. ₽</CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="gap-1 border-emerald-200 bg-emerald-50 text-emerald-700">
                <TrendingUp className="h-3 w-3" /> +52% за год
              </Badge>
              <Button variant="outline" size="sm" className="h-8 gap-1.5">
                <Download className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Экспорт</span>
              </Button>
            </div>
          </CardHeader>
          <CardContent className="h-[280px] pt-4">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={revenueByMonth} margin={{ top: 5, right: 10, left: -10, bottom: 0 }}>
                <defs>
                  <linearGradient id="gRevenue" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#10b981" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="#10b981" stopOpacity={0.02} />
                  </linearGradient>
                  <linearGradient id="gCosts" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#d97706" stopOpacity={0.25} />
                    <stop offset="100%" stopColor="#d97706" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--erp-grid)" vertical={false} />
                <XAxis dataKey="month" tick={{ fontSize: 12, fill: '#71717a' }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 12, fill: '#71717a' }} axisLine={false} tickLine={false} />
                <Tooltip
                  formatter={(value: number | string, name: string) => [
                    `${Number(value).toLocaleString('ru-RU')} тыс. ₽`,
                    name === 'revenue' ? 'Выручка' : name === 'costs' ? 'Затраты' : 'Прибыль',
                  ]}
                  contentStyle={{ borderRadius: 10, border: '1px solid var(--border)', background: 'var(--popover)', color: 'var(--popover-foreground)', fontSize: 13 }}
                />
                <Area type="monotone" dataKey="revenue" stroke="#059669" strokeWidth={2.5} fill="url(#gRevenue)" name="revenue" />
                <Area type="monotone" dataKey="costs" stroke="#d97706" strokeWidth={2} fill="url(#gCosts)" name="costs" />
              </AreaChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Структура продаж</CardTitle>
            <CardDescription>Доля категорий за декабрь</CardDescription>
          </CardHeader>
          <CardContent className="h-[280px]">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={salesByCategory}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={55}
                  outerRadius={85}
                  paddingAngle={3}
                  strokeWidth={0}
                >
                  {salesByCategory.map((_, i) => (
                    <Cell key={i} fill={pieColors[i % pieColors.length]} />
                  ))}
                </Pie>
                <Tooltip
                  formatter={(value: number | string, name: string) => [`${value}%`, name]}
                  contentStyle={{ borderRadius: 10, border: '1px solid var(--border)', background: 'var(--popover)', color: 'var(--popover-foreground)', fontSize: 13 }}
                />
              </PieChart>
            </ResponsiveContainer>
            <div className="mt-1 grid grid-cols-1 gap-1.5 text-xs">
              {salesByCategory.map((c, i) => (
                <div key={c.name} className="flex items-center justify-between">
                  <span className="flex items-center gap-2 text-muted-foreground">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: pieColors[i % pieColors.length] }} />
                    {c.name}
                  </span>
                  <span className="font-semibold">{c.value}%</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Нижний ряд: активность и склады */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Лента активности */}
        <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
          <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
            <CardTitle className="text-base">Лента событий</CardTitle>
            <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground">
              <MoreHorizontal className="h-4 w-4" />
            </Button>
          </CardHeader>
          <CardContent className="max-h-80 space-y-1 overflow-y-auto pr-2 erp-scroll">
            {activityData.map((a) => (
              <div key={a.id} className="flex items-start gap-3 rounded-lg p-2 transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/60">
                <span className={cn('mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full', activityTones[a.kind])}>
                  {activityIcons[a.kind]}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm leading-snug">
                    <span className="font-medium">{a.user}</span>{' '}
                    <span className="text-muted-foreground">{a.action}</span>
                  </p>
                  <p className="truncate text-xs text-muted-foreground">{a.target}</p>
                </div>
                <span className="shrink-0 text-[11px] text-muted-foreground">{a.time}</span>
              </div>
            ))}
          </CardContent>
        </Card>

        {/* Заполненность складов */}
        <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Заполненность складов</CardTitle>
            <CardDescription>Данные на сегодня, 09:00</CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            {warehouseStats.map((w) => (
              <div key={w.name}>
                <div className="mb-1.5 flex items-center justify-between text-sm">
                  <span className="font-medium">{w.name}</span>
                  <span className={cn('text-xs font-semibold', w.fill > 80 ? 'text-red-600' : 'text-muted-foreground')}>
                    {w.fill}% · {w.pallets}/{w.capacity} паллет
                  </span>
                </div>
                <Progress
                  value={w.fill}
                  className={cn('h-2.5 [&>div]:bg-emerald-500', w.fill > 80 && '[&>div]:bg-red-500')}
                />
              </div>
            ))}
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
              <span className="font-semibold">Внимание:</span> склад В заполнен на 87% — запланируйте отгрузку
              транзитных позиций до 20.12.
            </div>
            <Button variant="outline" className="w-full gap-1.5">
              Открыть склад <ArrowUpRight className="h-3.5 w-3.5" />
            </Button>
          </CardContent>
        </Card>
      </div>

      {/* Канбан задач с drag-and-drop */}
      <TaskKanban />
    </div>
  )
}

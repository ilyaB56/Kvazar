'use client'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ViewHeader, InvoiceStatusBadge, DraftNote } from './shared'
import { invoicesData, cashflowData, expenseStructure, fmtMoney } from '@/lib/erp-data'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  Plus,
  Download,
  Wallet,
  Landmark,
  Receipt,
  Clock8,
  Send,
  FileCheck2,
} from 'lucide-react'
import { cn } from '@/lib/utils'

const expenseColors = ['#059669', '#0d9488', '#d97706', '#ea580c', '#65a30d']

export function FinanceView() {
  const totalPaid = invoicesData.filter((i) => i.status === 'paid').reduce((s, i) => s + i.amount, 0)
  const totalPending = invoicesData.filter((i) => i.status === 'pending').reduce((s, i) => s + i.amount, 0)
  const totalOverdue = invoicesData.filter((i) => i.status === 'overdue').reduce((s, i) => s + i.amount, 0)

  return (
    <div className="space-y-6">
      <ViewHeader title="Финансы" subtitle="Счета, платежи и денежный поток">
        <Button variant="outline" size="sm" className="gap-1.5">
          <Download className="h-4 w-4" />
          <span className="hidden sm:inline">1С-Обмен</span>
        </Button>
        <Button size="sm" className="gap-1.5 bg-emerald-600 text-white hover:bg-emerald-700">
          <Plus className="h-4 w-4" /> Выставить счёт
        </Button>
      </ViewHeader>

      {/* KPI финансы */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[
          { label: 'Поступления за месяц', value: fmtMoney(totalPaid), icon: <Wallet className="h-5 w-5" />, tone: 'bg-emerald-50 text-emerald-600 dark:bg-emerald-950/60 dark:text-emerald-400', sub: '18 платежей' },
          { label: 'К получению', value: fmtMoney(totalPending), icon: <Clock8 className="h-5 w-5" />, tone: 'bg-amber-50 text-amber-600 dark:bg-amber-950/60 dark:text-amber-400', sub: '3 счёта' },
          { label: 'Просрочено', value: fmtMoney(totalOverdue), icon: <Receipt className="h-5 w-5" />, tone: 'bg-red-50 text-red-600 dark:bg-red-950/60 dark:text-red-400', sub: '1 контрагент' },
          { label: 'Свободный остаток', value: '8 420 300 ₽', icon: <Landmark className="h-5 w-5" />, tone: 'bg-teal-50 text-teal-600 dark:bg-teal-950/60 dark:text-teal-400', sub: 'по 3 счетам фирмы' },
        ].map((k) => (
          <Card key={k.label} className="border-zinc-200 shadow-sm dark:border-zinc-800">
            <CardContent className="p-5">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">{k.label}</span>
                <span className={cn('flex h-9 w-9 items-center justify-center rounded-lg', k.tone)}>{k.icon}</span>
              </div>
              <p className="mt-3 text-xl font-bold">{k.value}</p>
              <p className="mt-1 text-xs text-muted-foreground">{k.sub}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <Tabs defaultValue="invoices" className="space-y-4">
        <TabsList className="bg-zinc-100 dark:bg-zinc-800/70">
          <TabsTrigger value="invoices">Счета</TabsTrigger>
          <TabsTrigger value="cashflow">Денежный поток</TabsTrigger>
          <TabsTrigger value="expenses">Расходы</TabsTrigger>
        </TabsList>

        {/* Счета */}
        <TabsContent value="invoices" className="mt-0">
          <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-zinc-50/80 hover:bg-zinc-50/80 dark:bg-zinc-900/50">
                      <TableHead>№ счёта</TableHead>
                      <TableHead>Контрагент</TableHead>
                      <TableHead className="hidden sm:table-cell">Выставлен</TableHead>
                      <TableHead className="hidden md:table-cell">Срок оплаты</TableHead>
                      <TableHead className="text-right">Сумма</TableHead>
                      <TableHead>Статус</TableHead>
                      <TableHead className="hidden lg:table-cell w-[90px]" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {invoicesData.map((inv) => (
                      <TableRow key={inv.id}>
                        <TableCell className="font-medium text-emerald-700">{inv.id}</TableCell>
                        <TableCell className="text-sm">{inv.counterparty}</TableCell>
                        <TableCell className="hidden sm:table-cell text-sm text-muted-foreground whitespace-nowrap">{inv.date}</TableCell>
                        <TableCell className="hidden md:table-cell text-sm text-muted-foreground whitespace-nowrap">{inv.dueDate}</TableCell>
                        <TableCell className="text-right font-semibold whitespace-nowrap">{fmtMoney(inv.amount)}</TableCell>
                        <TableCell><InvoiceStatusBadge status={inv.status} /></TableCell>
                        <TableCell className="hidden lg:table-cell">
                          <div className="flex items-center gap-1">
                            <Button variant="ghost" size="icon" className="h-7 w-7" title="Отправить">
                              <Send className="h-3.5 w-3.5" />
                            </Button>
                            <Button variant="ghost" size="icon" className="h-7 w-7" title="PDF">
                              <FileCheck2 className="h-3.5 w-3.5" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Денежный поток */}
        <TabsContent value="cashflow" className="mt-0">
          <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Поступления и списания</CardTitle>
              <CardDescription>Последние 6 месяцев, тыс. ₽</CardDescription>
            </CardHeader>
            <CardContent className="h-[320px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={cashflowData} margin={{ top: 5, right: 10, left: -10, bottom: 0 }} barGap={6}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--erp-grid)" vertical={false} />
                  <XAxis dataKey="month" tick={{ fontSize: 12, fill: '#71717a' }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 12, fill: '#71717a' }} axisLine={false} tickLine={false} />
                  <Tooltip
                    formatter={(value: number | string, name: string) => [
                      `${Number(value).toLocaleString('ru-RU')} тыс. ₽`,
                      name === 'in' ? 'Поступления' : 'Списания',
                    ]}
                    contentStyle={{ borderRadius: 10, border: '1px solid var(--border)', background: 'var(--popover)', color: 'var(--popover-foreground)', fontSize: 13 }}
                  />
                  <Bar dataKey="in" name="in" fill="#059669" radius={[6, 6, 0, 0]} maxBarSize={26} />
                  <Bar dataKey="out" name="out" fill="#d97706" radius={[6, 6, 0, 0]} maxBarSize={26} />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Структура расходов */}
        <TabsContent value="expenses" className="mt-0">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
              <CardHeader className="pb-2">
                <CardTitle className="text-base">Структура расходов</CardTitle>
                <CardDescription>Доли за декабрь</CardDescription>
              </CardHeader>
              <CardContent className="flex h-[300px] items-center">
                <div className="h-full flex-1">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={expenseStructure} dataKey="value" nameKey="name" innerRadius={50} outerRadius={85} paddingAngle={3} strokeWidth={0}>
                        {expenseStructure.map((_, i) => (
                          <Cell key={i} fill={expenseColors[i % expenseColors.length]} />
                        ))}
                      </Pie>
                      <Tooltip formatter={(v: number | string, n: string) => [`${v}%`, n]} contentStyle={{ borderRadius: 10, border: '1px solid var(--border)', background: 'var(--popover)', color: 'var(--popover-foreground)', fontSize: 13 }} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="w-40 space-y-2 text-sm">
                  {expenseStructure.map((e, i) => (
                    <div key={e.name} className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-2 text-muted-foreground">
                        <span className="h-2.5 w-2.5 rounded-full" style={{ background: expenseColors[i % expenseColors.length] }} />
                        {e.name}
                      </span>
                      <span className="font-semibold">{e.value}%</span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
              <CardHeader className="pb-2">
                <CardTitle className="text-base">Бюджеты направлений</CardTitle>
                <CardDescription>Исполнение на декабрь</CardDescription>
              </CardHeader>
              <CardContent className="space-y-5 pt-2">
                {[
                  { name: 'Закупки', used: 82, budget: '9,1 млн ₽' },
                  { name: 'ФОТ', used: 95, budget: '3,8 млн ₽' },
                  { name: 'Логистика', used: 64, budget: '1,9 млн ₽' },
                  { name: 'Маркетинг', used: 38, budget: '1,4 млн ₽' },
                ].map((b) => (
                  <div key={b.name}>
                    <div className="mb-1.5 flex items-center justify-between text-sm">
                      <span className="font-medium">{b.name}</span>
                      <span className="text-xs text-muted-foreground">
                        израсходовано <span className={cn('font-semibold', b.used > 90 ? 'text-red-600' : 'text-foreground')}>{b.used}%</span> · {b.budget}
                      </span>
                    </div>
                    <Progress value={b.used} className={cn('h-2.5 [&>div]:bg-emerald-500', b.used > 90 && '[&>div]:bg-red-500')} />
                  </div>
                ))}
                <Badge variant="outline" className="border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
                  ФОТ близок к лимиту бюджета — 95%
                </Badge>
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>

      <DraftNote text="Макет: платёжный календарь, акты сверки, банковские выписки и согласование заявок на оплату." />
    </div>
  )
}

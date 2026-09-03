'use client'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ViewHeader, DraftNote } from './shared'
import { ReportBuilder } from './report-builder'
import { reportLibrary, managerSales, revenueByMonth } from '@/lib/erp-data'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  Play,
  Download,
  CalendarClock,
  FileBarChart2,
  Star,
  MoreHorizontal,
  Percent,
} from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'

const tagTones: Record<string, string> = {
  'Финансы': 'border-amber-200 bg-amber-50 text-amber-700',
  'Склад': 'border-teal-200 bg-teal-50 text-teal-700',
  'Продажи': 'border-emerald-200 bg-emerald-50 text-emerald-700',
  'HR': 'border-sky-200 bg-sky-50 text-sky-700',
  'Производство': 'border-orange-200 bg-orange-50 text-orange-700',
}

export function ReportsView() {
  const [builderOpen, setBuilderOpen] = useState(false)

  return (
    <div className="space-y-6">
      <ViewHeader title="Отчёты" subtitle="Конструктор отчётности и аналитика">
        <Button variant="outline" size="sm" className="gap-1.5">
          <CalendarClock className="h-4 w-4" />
          <span className="hidden sm:inline">Расписания</span>
        </Button>
        <Button size="sm" className="gap-1.5 bg-emerald-600 text-white hover:bg-emerald-700" onClick={() => setBuilderOpen(true)}>
          <FileBarChart2 className="h-4 w-4" /> Собрать отчёт
        </Button>
      </ViewHeader>

      {/* Избранные отчёты */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {reportLibrary.map((r) => (
          <Card key={r.id} className="group border-zinc-200 shadow-sm dark:border-zinc-800 transition-all hover:-translate-y-0.5 hover:shadow-md">
            <CardHeader className="pb-2">
              <div className="flex items-start justify-between">
                <CardTitle className="text-sm font-semibold leading-snug">{r.name}</CardTitle>
                <div className="flex items-center gap-1">
                  <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100">
                    <Star className="h-3.5 w-3.5" />
                  </Button>
                  <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground">
                    <MoreHorizontal className="h-4 w-4" />
                  </Button>
                </div>
              </div>
              <CardDescription>Периодичность: {r.period}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center gap-2">
                <Badge variant="outline" className={tagTones[r.tag]}>{r.tag}</Badge>
                <span className="text-xs text-muted-foreground">обновлён {r.updated}</span>
              </div>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" className="h-8 flex-1 gap-1.5">
                  <Play className="h-3.5 w-3.5" /> Запустить
                </Button>
                <Button size="sm" variant="ghost" className="h-8 gap-1.5">
                  <Download className="h-3.5 w-3.5" /> XLSX
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Аналитика */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Выполнение плана менеджерами</CardTitle>
            <CardDescription>Декабрь, тыс. ₽</CardDescription>
          </CardHeader>
          <CardContent className="h-[300px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={managerSales} margin={{ top: 5, right: 10, left: -10, bottom: 0 }} barGap={5}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--erp-grid)" vertical={false} />
                <XAxis dataKey="name" tick={{ fontSize: 12, fill: '#71717a' }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 12, fill: '#71717a' }} axisLine={false} tickLine={false} />
                <Tooltip
                  formatter={(v: number | string, n: string) => [
                    `${Number(v).toLocaleString('ru-RU')} тыс. ₽`,
                    n === 'plan' ? 'План' : 'Факт',
                  ]}
                  contentStyle={{ borderRadius: 10, border: '1px solid var(--border)', background: 'var(--popover)', color: 'var(--popover-foreground)', fontSize: 13 }}
                />
                <Legend
                  formatter={(v: string) => (v === 'plan' ? 'План' : 'Факт')}
                  wrapperStyle={{ fontSize: 12 }}
                />
                <Bar dataKey="plan" fill="#d4d4d8" radius={[6, 6, 0, 0]} maxBarSize={28} />
                <Bar dataKey="fact" fill="#059669" radius={[6, 6, 0, 0]} maxBarSize={28} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
          <CardHeader className="flex-row items-start justify-between space-y-0 pb-2">
            <div>
              <CardTitle className="text-base">Рентабельность</CardTitle>
              <CardDescription>Маржинальность по месяцам, %</CardDescription>
            </div>
            <Badge variant="outline" className="gap-1 border-emerald-200 bg-emerald-50 text-emerald-700">
              <Percent className="h-3 w-3" /> средняя 39%
            </Badge>
          </CardHeader>
          <CardContent className="h-[300px]">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart
                data={revenueByMonth.map((m) => ({
                  month: m.month,
                  margin: Math.round((m.profit / m.revenue) * 100),
                }))}
                margin={{ top: 5, right: 10, left: -10, bottom: 0 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="var(--erp-grid)" vertical={false} />
                <XAxis dataKey="month" tick={{ fontSize: 12, fill: '#71717a' }} axisLine={false} tickLine={false} />
                <YAxis domain={[20, 50]} tick={{ fontSize: 12, fill: '#71717a' }} axisLine={false} tickLine={false} />
                <Tooltip
                  formatter={(v: number | string) => [`${v}%`, 'Маржа']}
                  contentStyle={{ borderRadius: 10, border: '1px solid var(--border)', background: 'var(--popover)', color: 'var(--popover-foreground)', fontSize: 13 }}
                />
                <Line
                  type="monotone"
                  dataKey="margin"
                  stroke="#0d9488"
                  strokeWidth={2.5}
                  dot={{ r: 3, fill: '#0d9488' }}
                  activeDot={{ r: 5 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      <DraftNote text="Макет: конструктор отчёта открыт кнопкой «Собрать отчёт». Здесь будет подписка на рассылку по e-mail." />

      {/* Конструктор отчёта */}
      <ReportBuilder open={builderOpen} onOpenChange={setBuilderOpen} />
    </div>
  )
}

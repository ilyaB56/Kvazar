'use client'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Progress } from '@/components/ui/progress'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { ViewHeader, DraftNote } from './shared'
import { EmployeeDialog } from './employee-dialog'
import { employeesData, departments, attendance, fmtMoney, type EmployeeRow } from '@/lib/erp-data'
import {
  Plus,
  UserPlus,
  CalendarCheck2,
  FileSpreadsheet,
  Mail,
  Phone,
  MapPin,
  Briefcase,
  GraduationCap,
  HeartPulse,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useState } from 'react'

const empStatusMap: Record<string, { label: string; className: string }> = {
  active: { label: 'На работе', className: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-900' },
  remote: { label: 'Удалённо', className: 'border-teal-200 bg-teal-50 text-teal-700 dark:bg-teal-950/60 dark:text-teal-300 dark:border-teal-900' },
  vacation: { label: 'Отпуск', className: 'border-amber-200 bg-amber-50 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300 dark:border-amber-900' },
  sick: { label: 'Больничный', className: 'border-red-200 bg-red-50 text-red-600 dark:bg-red-950/60 dark:text-red-400 dark:border-red-900' },
}

const avatarTones = ['bg-emerald-100 text-emerald-700', 'bg-teal-100 text-teal-700', 'bg-amber-100 text-amber-700', 'bg-orange-100 text-orange-700', 'bg-lime-100 text-lime-700']

export function HrView() {
  const [selected, setSelected] = useState<EmployeeRow | null>(null)
  const totalHeadcount = departments.reduce((s, d) => s + d.headcount, 0)
  const totalPlan = departments.reduce((s, d) => s + d.plan, 0)
  const payroll = employeesData.reduce((s, e) => s + e.salary, 0)

  return (
    <div className="space-y-6">
      <ViewHeader title="Персонал" subtitle="Сотрудники, отделы, табель и найм">
        <Button variant="outline" size="sm" className="gap-1.5">
          <FileSpreadsheet className="h-4 w-4" />
          <span className="hidden sm:inline">Табель</span>
        </Button>
        <Button variant="outline" size="sm" className="gap-1.5">
          <CalendarCheck2 className="h-4 w-4" />
          <span className="hidden sm:inline">Отпуска</span>
        </Button>
        <Button size="sm" className="gap-1.5 bg-emerald-600 text-white hover:bg-emerald-700">
          <UserPlus className="h-4 w-4" /> Нанять
        </Button>
      </ViewHeader>

      {/* Сводка */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
          <CardContent className="p-5">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Штат</span>
              <Briefcase className="h-4 w-4 text-emerald-600" />
            </div>
            <p className="mt-2 text-2xl font-bold">{totalHeadcount} <span className="text-base font-medium text-muted-foreground">/ {totalPlan}</span></p>
            <Progress value={(totalHeadcount / totalPlan) * 100} className="mt-2 h-2 [&>div]:bg-emerald-500" />
            <p className="mt-1.5 text-xs text-muted-foreground">Укомплектованность {(totalHeadcount / totalPlan * 100).toFixed(0)}%</p>
          </CardContent>
        </Card>
        <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
          <CardContent className="p-5">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">ФОТ в месяц</span>
              <GraduationCap className="h-4 w-4 text-teal-600" />
            </div>
            <p className="mt-2 text-2xl font-bold">{fmtMoney(payroll)}</p>
            <p className="mt-1.5 text-xs text-muted-foreground">Средняя — {fmtMoney(payroll / employeesData.length)}</p>
          </CardContent>
        </Card>
        <Card className="border-zinc-200 shadow-sm dark:border-zinc-800 sm:col-span-2">
          <CardContent className="p-5">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Сегодня на учёте</span>
              <HeartPulse className="h-4 w-4 text-orange-600" />
            </div>
            <div className="mt-3 grid grid-cols-4 gap-2 text-center">
              {attendance.map((a) => (
                <div key={a.label} className="rounded-lg bg-zinc-50 py-2.5 dark:bg-zinc-800/50">
                  <p className={cn('text-xl font-bold', a.tone)}>{a.value}</p>
                  <p className="mt-0.5 text-[11px] leading-tight text-muted-foreground">{a.label}</p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        {/* Таблица сотрудников */}
        <Card className="border-zinc-200 shadow-sm dark:border-zinc-800 xl:col-span-2">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Сотрудники</CardTitle>
            <CardDescription>Активные записи из кадрового реестра</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="bg-zinc-50/80 hover:bg-zinc-50/80 dark:bg-zinc-900/50">
                    <TableHead>Сотрудник</TableHead>
                    <TableHead className="hidden md:table-cell">Подразделение</TableHead>
                    <TableHead className="hidden lg:table-cell">Контакты</TableHead>
                    <TableHead className="text-right">Оклад</TableHead>
                    <TableHead>Статус</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {employeesData.map((e, i) => (
                    <TableRow
                      key={e.id}
                      className="group cursor-pointer"
                      onClick={() => setSelected(e)}
                    >
                      <TableCell>
                        <div className="flex items-center gap-3">
                          <Avatar className="h-9 w-9">
                            <AvatarFallback className={avatarTones[i % avatarTones.length]}>
                              {e.name.split(' ').map((p) => p[0]).join('')}
                            </AvatarFallback>
                          </Avatar>
                          <div>
                            <p className="text-sm font-medium group-hover:underline">{e.name}</p>
                            <p className="text-xs text-muted-foreground">{e.position}</p>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell className="hidden md:table-cell">
                        <Badge variant="secondary" className="font-normal">{e.department}</Badge>
                      </TableCell>
                      <TableCell className="hidden lg:table-cell">
                        <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><Mail className="h-3 w-3" /> {e.email}</p>
                        <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground"><Phone className="h-3 w-3" /> {e.phone}</p>
                      </TableCell>
                      <TableCell className="text-right text-sm font-semibold whitespace-nowrap">{fmtMoney(e.salary)}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className={empStatusMap[e.status].className}>
                          {empStatusMap[e.status].label}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>

        {/* Отделы */}
        <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Отделы</CardTitle>
            <CardDescription>Штат и руководители</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {departments.map((d) => (
              <div key={d.name} className="rounded-lg border border-zinc-100 p-3 transition-colors hover:border-emerald-200 hover:bg-emerald-50/40 dark:border-zinc-800 dark:hover:border-emerald-900 dark:hover:bg-emerald-950/20">
                <div className="flex items-center justify-between">
                  <span className="flex items-center gap-2 text-sm font-medium">
                    <span className={cn('h-2.5 w-2.5 rounded-full', d.color)} />
                    {d.name}
                  </span>
                  <span className="text-xs font-semibold text-muted-foreground">{d.headcount}/{d.plan}</span>
                </div>
                <Progress value={(d.headcount / d.plan) * 100} className="mt-2 h-1.5 [&>div]:bg-emerald-500" />
                <p className="mt-2 flex items-center gap-1 text-xs text-muted-foreground">
                  <MapPin className="h-3 w-3" /> Рук. {d.head} · 3 вакансии открыты
                </p>
              </div>
            ))}
            <Button variant="outline" className="w-full gap-1.5">
              <Plus className="h-4 w-4" /> Создать отдел
            </Button>
          </CardContent>
        </Card>
      </div>

      <DraftNote text="Макет: клик по сотруднику открывает карточку. Здесь будут график отпусков, KPI и расчётные листки." />

      {/* Карточка сотрудника */}
      <EmployeeDialog employee={selected} onClose={() => setSelected(null)} />
    </div>
  )
}

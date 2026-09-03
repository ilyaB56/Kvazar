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
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { getEmployeeProfile, fmtMoney, type EmployeeRow } from '@/lib/erp-data'
import {
  Mail,
  Phone,
  MapPin,
  Cake,
  CalendarDays,
  Award,
  FileText,
  MessageSquare,
  Pencil,
  X,
  TrendingUp,
  Briefcase,
} from 'lucide-react'
import { cn } from '@/lib/utils'

const empStatusMap: Record<string, { label: string; className: string }> = {
  active: { label: 'На работе', className: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-900' },
  remote: { label: 'Удалённо', className: 'border-teal-200 bg-teal-50 text-teal-700 dark:bg-teal-950/60 dark:text-teal-300 dark:border-teal-900' },
  vacation: { label: 'Отпуск', className: 'border-amber-200 bg-amber-50 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300 dark:border-amber-900' },
  sick: { label: 'Больничный', className: 'border-red-200 bg-red-50 text-red-600 dark:bg-red-950/60 dark:text-red-400 dark:border-red-900' },
}

export function EmployeeDialog({
  employee,
  onClose,
}: {
  employee: EmployeeRow | null
  onClose: () => void
}) {
  const p = employee ? getEmployeeProfile(employee) : null
  const initials = p ? p.name.split(' ').map((x) => x[0]).join('') : ''

  return (
    <Dialog open={!!employee} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-xl erp-scroll">
        {p && (
          <>
            <DialogHeader>
              <div className="flex items-center gap-4 pr-6">
                <Avatar className="h-14 w-14 border-2 border-emerald-100 dark:border-emerald-900">
                  <AvatarFallback className="bg-emerald-100 text-lg font-bold text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                    {initials}
                  </AvatarFallback>
                </Avatar>
                <div className="min-w-0">
                  <DialogTitle className="text-lg">{p.name}</DialogTitle>
                  <DialogDescription className="mt-0.5">{p.position}</DialogDescription>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    <Badge variant="outline" className={empStatusMap[p.status].className}>
                      {empStatusMap[p.status].label}
                    </Badge>
                    <Badge variant="secondary" className="font-normal">{p.department}</Badge>
                    <span className="text-xs text-muted-foreground">{p.id}</span>
                  </div>
                </div>
              </div>
            </DialogHeader>

            {/* Контакты */}
            <div className="grid grid-cols-1 gap-2 rounded-xl border p-4 sm:grid-cols-2 dark:border-zinc-800">
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Mail className="h-4 w-4 shrink-0 text-emerald-600" />
                <span className="truncate">{p.email}</span>
              </p>
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Phone className="h-4 w-4 shrink-0 text-emerald-600" /> {p.phone}
              </p>
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <MapPin className="h-4 w-4 shrink-0 text-emerald-600" /> {p.city}
              </p>
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Cake className="h-4 w-4 shrink-0 text-emerald-600" /> {p.birthDate}
              </p>
            </div>

            {/* Показатели */}
            <div className="grid grid-cols-3 gap-2">
              <div className="rounded-xl border p-3 text-center dark:border-zinc-800">
                <TrendingUp className="mx-auto h-4 w-4 text-emerald-600" />
                <p className={cn('mt-1 text-lg font-bold', p.performance >= 100 ? 'text-emerald-600' : 'text-amber-600')}>
                  {p.performance}%
                </p>
                <p className="text-[11px] leading-tight text-muted-foreground">выполнение KPI</p>
              </div>
              <div className="rounded-xl border p-3 text-center dark:border-zinc-800">
                <Briefcase className="mx-auto h-4 w-4 text-teal-600" />
                <p className="mt-1 text-lg font-bold">{p.dealsClosed || '—'}</p>
                <p className="text-[11px] leading-tight text-muted-foreground">сделок за год</p>
              </div>
              <div className="rounded-xl border p-3 text-center dark:border-zinc-800">
                <CalendarDays className="mx-auto h-4 w-4 text-amber-600" />
                <p className="mt-1 text-lg font-bold">{p.vacationLeft}</p>
                <p className="text-[11px] leading-tight text-muted-foreground">дней отпуска</p>
              </div>
            </div>

            {/* KPI прогресс */}
            <div>
              <div className="mb-1.5 flex items-center justify-between text-xs text-muted-foreground">
                <span>План квартала</span>
                <span className="font-semibold text-foreground">{p.performance}%</span>
              </div>
              <Progress
                value={Math.min(100, p.performance)}
                className={cn('h-2.5', p.performance >= 100 ? '[&>div]:bg-emerald-500' : '[&>div]:bg-amber-500')}
              />
            </div>

            {/* Достижения и документы */}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="rounded-xl border p-3 dark:border-zinc-800">
                <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  <Award className="h-3.5 w-3.5 text-amber-500" /> Достижения
                </p>
                <ul className="space-y-1.5">
                  {p.achievements.map((a) => (
                    <li key={a} className="text-sm">🏆 {a}</li>
                  ))}
                  {p.achievements.length === 0 && <li className="text-sm text-muted-foreground">Пока нет</li>}
                </ul>
              </div>
              <div className="rounded-xl border p-3 dark:border-zinc-800">
                <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  <FileText className="h-3.5 w-3.5 text-emerald-600" /> Документы
                </p>
                <ul className="space-y-1.5">
                  {p.documents.map((d) => (
                    <li key={d}>
                      <button className="text-sm text-emerald-700 underline-offset-2 hover:underline dark:text-emerald-400">
                        {d}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            </div>

            {/* Трудоустройство */}
            <p className="text-xs text-muted-foreground">
              В компании с <span className="font-medium text-foreground">{p.since}</span> · оклад{' '}
              <span className="font-medium text-foreground">{fmtMoney(p.salary)}</span>/мес
            </p>

            <DialogFooter className="gap-2 sm:gap-0">
              <div className="flex w-full flex-col gap-2 sm:flex-row sm:justify-between">
                <div className="flex gap-2">
                  <Button size="sm" className="gap-1.5 bg-emerald-600 text-white hover:bg-emerald-700">
                    <MessageSquare className="h-3.5 w-3.5" /> Написать
                  </Button>
                  <Button variant="outline" size="sm" className="gap-1.5">
                    <Pencil className="h-3.5 w-3.5" /> Редактировать
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

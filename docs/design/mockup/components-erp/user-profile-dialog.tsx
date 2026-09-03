'use client'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { Switch } from '@/components/ui/switch'
import {
  Mail,
  Phone,
  MapPin,
  Cake,
  CalendarCheck,
  ShieldCheck,
  ShieldAlert,
  MonitorSmartphone,
  LogOut,
  Pencil,
  Crown,
  Monitor,
} from 'lucide-react'
import { useState } from 'react'
import { useToast } from '@/hooks/use-toast'
import {
  currentUserProfile,
  permissionMatrix,
  permModules,
  permValueMap,
} from '@/lib/erp-data'
import { cn } from '@/lib/utils'

// Права текущего пользователя (роль head) — используются для сводки доступа
const rolePerms = permissionMatrix[currentUserProfile.role]

export function UserProfileDialog({
  open,
  onOpenChange,
  onLogout,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  onLogout?: () => void
}) {
  const { toast } = useToast()
  const u = currentUserProfile
  const [twoFactor, setTwoFactor] = useState(u.twoFactor)
  const [sessions, setSessions] = useState(u.sessions)

  const terminate = (id: string) => {
    const s = sessions.find((x) => x.id === id)
    setSessions((prev) => prev.filter((x) => x.id !== id))
    toast({
      title: 'Сессия завершена',
      description: `${s?.device} · ${s?.browser} принудительно разлогинен`,
      duration: 3000,
    })
  }

  const fullNameParts = u.fullName.split(' ')

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-xl erp-scroll">
        {open && (
          <>
            <DialogHeader>
              <div className="flex flex-wrap items-center gap-3 pr-6">
                <Avatar className="h-12 w-12 border border-emerald-200 dark:border-emerald-900">
                  <AvatarFallback className="bg-emerald-100 text-base font-bold text-emerald-700">
                    {u.initials}
                  </AvatarFallback>
                </Avatar>
                <div className="min-w-0">
                  <DialogTitle className="flex items-center gap-2 text-lg">
                    {u.name}
                    <Badge className="gap-1 border-transparent bg-teal-100 text-[11px] text-teal-800 hover:bg-teal-100 dark:bg-teal-950/70 dark:text-teal-300">
                      <Crown className="h-3 w-3" /> {u.roleName}
                    </Badge>
                  </DialogTitle>
                  <DialogDescription className="mt-0.5">
                    {u.position} · {u.department}
                  </DialogDescription>
                </div>
              </div>
            </DialogHeader>

            {/* Контакты */}
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {[
                { icon: <Mail className="h-3.5 w-3.5" />, label: 'E-mail', value: u.email },
                { icon: <Phone className="h-3.5 w-3.5" />, label: 'Телефон', value: u.phone },
                { icon: <MapPin className="h-3.5 w-3.5" />, label: 'Офис', value: u.office },
                { icon: <Cake className="h-3.5 w-3.5" />, label: 'День рождения', value: u.birthday },
                { icon: <CalendarCheck className="h-3.5 w-3.5" />, label: 'В компании с', value: u.since },
                { icon: <MonitorSmartphone className="h-3.5 w-3.5" />, label: 'Последний вход', value: u.lastLogin },
              ].map((c) => (
                <div
                  key={c.label}
                  className="flex items-start gap-2.5 rounded-lg border border-zinc-200 bg-zinc-50/60 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-900/50"
                >
                  <span className="mt-0.5 text-emerald-600 dark:text-emerald-400">{c.icon}</span>
                  <div className="min-w-0">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{c.label}</p>
                    <p className="truncate text-sm font-medium">{c.value}</p>
                  </div>
                </div>
              ))}
            </div>

            {/* Права доступа по разделам */}
            <div className="rounded-xl border p-4 dark:border-zinc-800">
              <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Права доступа · роль «{u.roleName}»
              </p>
              <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
                {permModules.map((m) => {
                  const perm = permValueMap[rolePerms[m.key]]
                  return (
                    <div key={m.key} className="flex items-center justify-between gap-2 text-sm">
                      <span className="truncate text-foreground/90">{m.label}</span>
                      <span className={cn('shrink-0 text-xs font-bold', perm.cls)}>{perm.short}</span>
                    </div>
                  )
                })}
              </div>
              <p className="mt-3 text-[11px] leading-snug text-muted-foreground">
                R/W — полный доступ · R/O — только просмотр · «—» — раздел скрыт.
                Изменение прав — в Настройках → Роли и права.
              </p>
            </div>

            {/* Безопасность */}
            <div className="rounded-xl border p-4 dark:border-zinc-800">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-start gap-2.5">
                  {twoFactor ? (
                    <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
                  ) : (
                    <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
                  )}
                  <div>
                    <p className="text-sm font-medium">Двухфакторная аутентификация</p>
                    <p className="text-xs text-muted-foreground">
                      {twoFactor ? 'Приложение-аутентификатор подключено' : 'Вход подтверждается только паролем — включите 2FA'}
                    </p>
                  </div>
                </div>
                <Switch
                  checked={twoFactor}
                  onCheckedChange={(v) => {
                    setTwoFactor(v)
                    toast({
                      title: v ? '2FA включена' : '2FA отключена',
                      description: v
                        ? 'Следующий вход потребует код из приложения'
                        : 'Вход по паролю без подтверждения (небезопасно)',
                      duration: 3000,
                    })
                  }}
                  aria-label="Двухфакторная аутентификация"
                />
              </div>

              <Separator className="my-4" />

              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Активные сессии ({sessions.length})
              </p>
              <div className="space-y-2">
                {sessions.map((s) => (
                  <div
                    key={s.id}
                    className="flex items-center gap-3 rounded-lg border border-zinc-200 px-3 py-2 dark:border-zinc-800"
                  >
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
                      <Monitor className="h-4 w-4" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="flex items-center gap-1.5 text-sm font-medium">
                        <span className="truncate">{s.device}</span>
                        {s.current && (
                          <Badge className="border-transparent bg-emerald-100 px-1.5 text-[10px] text-emerald-700 hover:bg-emerald-100 dark:bg-emerald-950/70 dark:text-emerald-300">
                            это устройство
                          </Badge>
                        )}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        {s.browser} · {s.location} · {s.lastActive}
                      </p>
                    </div>
                    {!s.current && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-8 shrink-0 gap-1 px-2 text-xs text-red-600 hover:bg-red-50 hover:text-red-700 dark:hover:bg-red-950/40 dark:text-red-400 dark:hover:text-red-300"
                        onClick={() => terminate(s.id)}
                      >
                        <LogOut className="h-3.5 w-3.5" /> Завершить
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            </div>

            <DialogFooter className="gap-2 sm:gap-0">
              <div className="flex w-full flex-col gap-2 sm:flex-row sm:justify-between">
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-1.5"
                  onClick={() =>
                    toast({
                      title: 'Профиль отправлен на согласование',
                      description: `Заявка на изменение данных: ${fullNameParts[0]} ${fullNameParts[1][0]}. · кадровая служба`,
                      duration: 3000,
                    })
                  }
                >
                  <Pencil className="h-3.5 w-3.5" /> Редактировать
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="gap-1.5 text-red-600 hover:bg-red-50 hover:text-red-700 dark:text-red-400 dark:hover:bg-red-950/40 dark:hover:text-red-300"
                  onClick={() => {
                    try {
                      window.localStorage.removeItem('erp-demo-auth')
                    } catch {
                      /* noop */
                    }
                    onLogout?.()
                  }}
                >
                  <LogOut className="h-3.5 w-3.5" /> Выйти из аккаунта
                </Button>
              </div>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

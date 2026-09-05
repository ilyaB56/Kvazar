'use client'

import { useState } from 'react'
import Image from 'next/image'
import { motion } from 'framer-motion'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import { Separator } from '@/components/ui/separator'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'
import {
  AlertCircle,
  Boxes,
  BrainCircuit,
  Briefcase,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  Lock,
  LogIn,
  Mail,
  PlugZap,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'

const features = [
  { icon: PlugZap, title: 'Интеграционное ядро', desc: 'Единственная точка выхода в интернет: API, банки, ЭДО, 1С' },
  { icon: BrainCircuit, title: 'Встроенный ИИ', desc: 'Обучается на внутренних документах, ищет и подсказывает' },
  { icon: Boxes, title: 'Склад и закупки', desc: 'Цифровой учёт остатков, резервы, автозаказ поставщикам' },
  { icon: Briefcase, title: 'CRM и управленческий учёт', desc: 'Сделки, клиенты, финансы и P&L в одном контуре' },
]

const brandStats = [
  { value: '1 274', label: 'активных клиентов' },
  { value: '12,48 млн ₽', label: 'выручка за месяц' },
  { value: '46', label: 'заказов в работе' },
]

export function LoginScreen({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = useState('v.naumenko@technoprom.ru')
  const [password, setPassword] = useState('demo1234')
  const [showPassword, setShowPassword] = useState(false)
  const [remember, setRemember] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState<'form' | 'guest' | null>(null)
  const { toast } = useToast()

  const signIn = (mode: 'form' | 'guest') => {
    if (mode === 'form') {
      if (!email.trim() || !password.trim()) {
        setError('Введите e-mail и пароль')
        return
      }
      if (password.length < 4) {
        setError('Пароль должен содержать минимум 4 символа')
        return
      }
    }
    setError(null)
    setLoading(mode)
    window.setTimeout(() => {
      setLoading(null)
      if (remember) {
        try {
          window.localStorage.setItem('erp-demo-auth', '1')
        } catch {
          /* noop — приватный режим */
        }
      }
      toast({
        title: mode === 'guest' ? 'Гостевой демо-вход' : 'Добро пожаловать!',
        description: 'Вы вошли в демо-контур ERP «Квазар»',
        duration: 2500,
      })
      onSuccess()
    }, 900)
  }

  return (
    <div className="min-h-screen bg-background lg:grid lg:grid-cols-[1.15fr_1fr]">
      {/* Левая брендовая панель (desktop) */}
      <aside className="relative hidden overflow-hidden bg-zinc-950 lg:flex lg:flex-col print:hidden">
        {/* декоративный фон: сетка + свечения */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-[0.16]"
          style={{
            backgroundImage:
              'linear-gradient(to right, rgb(255 255 255 / 0.35) 1px, transparent 1px), linear-gradient(to bottom, rgb(255 255 255 / 0.35) 1px, transparent 1px)',
            backgroundSize: '44px 44px',
            maskImage: 'radial-gradient(ellipse 90% 80% at 50% 40%, black 30%, transparent 75%)',
          }}
        />
        <div aria-hidden className="pointer-events-none absolute -left-24 top-1/4 h-96 w-96 rounded-full bg-emerald-500/20 blur-[110px]" />
        <div aria-hidden className="pointer-events-none absolute -right-16 bottom-0 h-80 w-80 rounded-full bg-teal-500/15 blur-[100px]" />
        <div aria-hidden className="pointer-events-none absolute -top-12 right-1/4 h-72 w-72 rounded-full bg-violet-500/25 blur-[100px]" />

        <div className="relative flex items-center gap-2.5 px-10 py-8">
          <span className="relative block h-10 w-10 shrink-0 overflow-hidden rounded-xl shadow-lg shadow-violet-500/25 ring-1 ring-white/15">
            <Image
              src="/brand/quasar-violet.png"
              alt="Логотип ERP «Квазар»: светлое ядро в орбитальных кольцах"
              fill
              sizes="40px"
              className="object-cover"
            />
          </span>
          <div className="leading-tight">
            <p className="text-base font-bold tracking-tight text-white">Квазар</p>
            <p className="text-xs text-zinc-400">ERP-платформа · интеграционное ядро</p>
          </div>
        </div>

        <div className="relative flex-1 px-10 py-6">
          <motion.h1
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.45, ease: 'easeOut' }}
            className="max-w-lg text-3xl font-bold leading-tight tracking-tight text-white xl:text-4xl"
          >
            Все модули — в одном ядре,
            <span className="block bg-gradient-to-r from-violet-300 to-fuchsia-400 bg-clip-text text-transparent">
              связь с миром — только через него
            </span>
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.45, delay: 0.08, ease: 'easeOut' }}
            className="mt-3 max-w-md text-sm leading-relaxed text-zinc-400"
          >
            Квазар — ERP-платформа: CRM, склад, закупки и управленческий учёт работают в закрытом контуре, а интеграционное ядро соединяет их с интернетом. Встроенный ИИ обучается на ваших документах.
          </motion.p>

          <div className="mt-8 grid max-w-lg gap-3 sm:grid-cols-2">
            {features.map((f, i) => (
              <motion.div
                key={f.title}
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4, delay: 0.12 + i * 0.07, ease: 'easeOut' }}
                className="rounded-xl border border-zinc-800/80 bg-zinc-900/60 p-4 backdrop-blur-sm transition-colors hover:border-violet-500/30"
              >
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-violet-500/15 text-violet-300">
                  <f.icon className="h-4 w-4" />
                </span>
                <p className="mt-2.5 text-sm font-semibold text-zinc-100">{f.title}</p>
                <p className="mt-1 text-xs leading-relaxed text-zinc-400">{f.desc}</p>
              </motion.div>
            ))}
          </div>
        </div>

        <div className="relative px-10 pb-8">
          <Separator className="mb-6 bg-zinc-800/80" />
          <div className="grid max-w-lg grid-cols-3 gap-4">
            {brandStats.map((s) => (
              <div key={s.label}>
                <p className="text-lg font-bold text-white">{s.value}</p>
                <p className="text-[11px] text-zinc-500">{s.label}</p>
              </div>
            ))}
          </div>
        </div>
      </aside>

      {/* Правая панель: форма входа */}
      <main className="relative flex min-h-screen flex-col items-center justify-center px-4 py-10 sm:px-8">
        {/* мобильный логотип */}
        <div className="mb-8 flex items-center gap-2.5 lg:hidden">
          <span className="relative block h-10 w-10 shrink-0 overflow-hidden rounded-xl shadow-lg shadow-violet-500/25 ring-1 ring-zinc-200">
            <Image
              src="/brand/quasar-violet.png"
              alt="Логотип ERP «Квазар»: светлое ядро в орбитальных кольцах"
              fill
              sizes="40px"
              className="object-cover"
            />
          </span>
          <div className="leading-tight">
            <p className="text-base font-bold tracking-tight">Квазар</p>
            <p className="text-xs text-muted-foreground">ERP-платформа · интеграционное ядро</p>
          </div>
        </div>

        <motion.div
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: 'easeOut' }}
          className="w-full max-w-[400px]"
        >
          <div className="rounded-2xl border border-zinc-200 bg-card p-6 shadow-xl shadow-zinc-950/5 sm:p-8 dark:border-zinc-800 dark:shadow-black/20">
            <h2 className="text-xl font-bold tracking-tight">Вход в систему</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Используйте корпоративную учётную запись
            </p>

            {/* демо-подсказка */}
            <div className="mt-5 flex items-start gap-2.5 rounded-xl border border-emerald-200 bg-emerald-50/70 px-3.5 py-3 dark:border-emerald-900 dark:bg-emerald-950/40">
              <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
              <p className="text-xs leading-relaxed text-emerald-800 dark:text-emerald-300">
                Это макет: данные демо. Введите любой пароль от 4 символов или войдите гостем.
              </p>
            </div>

            <form
              className="mt-5 space-y-4"
              onSubmit={(e) => {
                e.preventDefault()
                signIn('form')
              }}
            >
              <div className="space-y-1.5">
                <Label htmlFor="login-email" className="text-xs font-medium">E-mail</Label>
                <div className="relative">
                  <Mail className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    id="login-email"
                    type="email"
                    autoComplete="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@technoprom.ru"
                    className="h-10 pl-9"
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <Label htmlFor="login-password" className="text-xs font-medium">Пароль</Label>
                  <button
                    type="button"
                    className="text-xs font-medium text-emerald-700 transition-colors hover:text-emerald-600 dark:text-emerald-400 dark:hover:text-emerald-300"
                  >
                    Забыли пароль?
                  </button>
                </div>
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    id="login-password"
                    type={showPassword ? 'text' : 'password'}
                    autoComplete="current-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••"
                    className="h-10 pl-9 pr-10"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    aria-label={showPassword ? 'Скрыть пароль' : 'Показать пароль'}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
                  >
                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>

              {error && (
                <motion.div
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 dark:border-red-900 dark:bg-red-950/40"
                  role="alert"
                >
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-600 dark:text-red-400" />
                  <p className="text-xs font-medium text-red-700 dark:text-red-400">{error}</p>
                </motion.div>
              )}

              <div className="flex items-center justify-between">
                <label className="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
                  <Checkbox
                    id="remember"
                    checked={remember}
                    onCheckedChange={(v) => setRemember(v === true)}
                    className="data-[state=checked]:border-emerald-600 data-[state=checked]:bg-emerald-600 data-[state=checked]:text-white"
                  />
                  Запомнить меня
                </label>
                <span className="hidden items-center gap-1 text-[11px] text-muted-foreground sm:flex">
                  <ShieldCheck className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
                  2FA включена
                </span>
              </div>

              <Button
                type="submit"
                disabled={loading !== null}
                className="h-10 w-full gap-2 bg-emerald-600 text-base font-semibold text-white shadow-lg shadow-emerald-600/20 hover:bg-emerald-700 disabled:opacity-70"
              >
                {loading === 'form' ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" /> Проверяем учётные данные…
                  </>
                ) : (
                  <>
                    <LogIn className="h-4 w-4" /> Войти
                  </>
                )}
              </Button>
            </form>

            <div className="my-5 flex items-center gap-3">
              <Separator className="flex-1" />
              <span className="text-[11px] uppercase tracking-wide text-muted-foreground">или</span>
              <Separator className="flex-1" />
            </div>

            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <Button variant="outline" className="h-9 gap-2" disabled={loading !== null}>
                <KeyRound className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                Код из приложения
              </Button>
              <Button
                variant="outline"
                className="h-9 gap-2"
                disabled={loading !== null}
                onClick={() => signIn('guest')}
              >
                {loading === 'guest' ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <LogIn className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                )}
                Демо-вход гостем
              </Button>
            </div>
          </div>

          <p className={cn('mt-6 text-center text-xs text-muted-foreground')}>
            © 2025 ERP «Квазар» · макет интерфейса · v0.11.0
          </p>
          <p className="mt-1.5 flex items-center justify-center gap-1.5 text-[11px] text-muted-foreground">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
            Все сервисы контура работают штатно
          </p>
        </motion.div>
      </main>
    </div>
  )
}

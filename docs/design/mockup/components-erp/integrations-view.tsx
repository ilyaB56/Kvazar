'use client'

import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'
import { ViewHeader, DraftNote } from './shared'
import {
  Activity,
  ArrowDown,
  ArrowUp,
  Calculator,
  Check,
  Clock,
  Copy,
  FileCheck2,
  Globe,
  Landmark,
  Mail,
  Pause,
  Play,
  PlugZap,
  RefreshCw,
  ScanLine,
  ShieldAlert,
  ShieldCheck,
  Store,
  Unplug,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import {
  connectorsData,
  connectorStatusMap,
  coreEventsData,
  integrationCoreStats,
  fmtNum,
  type Connector,
  type ConnectorStatus,
  type CoreEvent,
} from '@/lib/erp-data'

const vendorIcon: Record<string, LucideIcon> = {
  'Учётная система': Calculator,
  Банк: Landmark,
  ЭДО: FileCheck2,
  Маркетплейс: Store,
  'E-commerce': Globe,
  Уведомления: Mail,
  Маркировка: ScanLine,
}

const toneIcon: Record<CoreEvent['tone'], LucideIcon> = {
  in: ArrowDown,
  out: ArrowUp,
  ok: RefreshCw,
  err: ShieldAlert,
}

const toneCls: Record<CoreEvent['tone'], string> = {
  in: 'text-sky-500',
  out: 'text-emerald-500',
  ok: 'text-zinc-400 dark:text-zinc-500',
  err: 'text-red-500',
}

const now = () => new Date().toLocaleTimeString('ru-RU', { hour12: false })

const feedTemplates: { action: string; tone: CoreEvent['tone']; status: string }[] = [
  { action: 'запрос остатков по 12 позициям', tone: 'in', status: '200 OK' },
  { action: 'выгрузка статусов заказов', tone: 'out', status: '200 OK' },
  { action: 'входящие: 2 УПД, 1 акт сверки', tone: 'in', status: '200 OK' },
  { action: 'отправка счёта клиенту', tone: 'out', status: '202 Принято' },
  { action: 'синхронизация номенклатуры · 46 позиций', tone: 'ok', status: '200 OK' },
  { action: 'подтверждение платежа по счёту', tone: 'in', status: '200 OK' },
  { action: 'новый заказ с сайта · создан в CRM', tone: 'in', status: '201 Created' },
  { action: 'выгрузка остатков на витрину', tone: 'out', status: '200 OK' },
]

const isolatedModules = ['Склад', 'Продажи', 'CRM', 'Персонал']

function CoreKpi({
  icon: Icon,
  label,
  value,
  sub,
}: {
  icon: LucideIcon
  label: string
  value: string
  sub: string
}) {
  return (
    <div className="rounded-xl border border-zinc-200 bg-card p-4 transition-colors hover:border-emerald-300 dark:border-zinc-800 dark:hover:border-emerald-800">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">{label}</p>
        <Icon className="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
      </div>
      <p className="mt-1.5 text-2xl font-bold tracking-tight">{value}</p>
      <p className="mt-0.5 text-[11px] text-muted-foreground">{sub}</p>
    </div>
  )
}

function StatusBadge({ status }: { status: ConnectorStatus }) {
  const m = connectorStatusMap[status]
  return (
    <span className={cn('inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[10px] font-bold', m.badge)}>
      <span className={cn('h-1.5 w-1.5 rounded-full', status === 'connected' && 'animate-pulse', status === 'error' && 'animate-pulse', m.iconCls.split(' ')[0])} />
      {m.label}
    </span>
  )
}

export function IntegrationsView() {
  const [connectors, setConnectors] = useState<Connector[]>(connectorsData)
  const [events, setEvents] = useState<CoreEvent[]>(coreEventsData)
  const [paused, setPaused] = useState(false)
  const [copied, setCopied] = useState(false)
  const { toast } = useToast()

  const idRef = useRef(coreEventsData.length + 1)
  const tickRef = useRef(0)
  const connIdxRef = useRef(0)

  const connectedCount = connectors.filter((c) => c.status === 'connected').length

  // Живой журнал: ядро генерирует события только по активным коннекторам
  useEffect(() => {
    if (paused) return
    const timer = window.setInterval(() => {
      tickRef.current += 1
      const active = connectors.filter((c) => c.status === 'connected')
      let ev: CoreEvent
      if (tickRef.current % 5 === 0 || active.length === 0) {
        const mod = isolatedModules[tickRef.current % isolatedModules.length]
        ev = {
          id: idRef.current++,
          time: now(),
          connector: 'Ядро',
          action: `прямая попытка доступа из модуля «${mod}» заблокирована`,
          tone: 'err',
          status: '403',
        }
      } else {
        const c = active[connIdxRef.current % active.length]
        connIdxRef.current += 1
        const tpl = feedTemplates[tickRef.current % feedTemplates.length]
        ev = { id: idRef.current++, time: now(), connector: c.name, ...tpl }
      }
      setEvents((prev) => [ev, ...prev].slice(0, 40))
    }, 3200)
    return () => window.clearInterval(timer)
  }, [paused, connectors])

  const patchConnector = (id: string, patch: Partial<Connector>) =>
    setConnectors((prev) => prev.map((c) => (c.id === id ? { ...c, ...patch } : c)))

  const connect = (c: Connector) => {
    patchConnector(c.id, { status: 'connected', lastSync: 'только что', eventsPerDay: c.eventsPerDay || 380 })
    toast({ title: 'Коннектор подключён', description: `${c.name} · первый обмен через ядро через 10 сек`, duration: 3000 })
  }

  const disconnect = (c: Connector) => {
    patchConnector(c.id, { status: 'off', lastSync: 'отключено вручную', eventsPerDay: 0 })
    toast({ title: 'Коннектор отключён', description: `${c.name} · очередь событий очищена`, duration: 3000 })
  }

  const reconnect = (c: Connector) => {
    patchConnector(c.id, { status: 'connected', lastSync: 'только что', eventsPerDay: 640 })
    toast({ title: 'Соединение восстановлено', description: `${c.name} · API-ключ обновлён, синхронизация идёт`, duration: 3000 })
  }

  const refreshAll = () => {
    setConnectors((prev) => prev.map((c) => (c.status === 'connected' ? { ...c, lastSync: 'только что' } : c)))
    toast({ title: 'Синхронизация запущена', description: `Обновление ${connectedCount} коннекторов через ядро`, duration: 3000 })
  }

  const copyGateway = async () => {
    try {
      await navigator.clipboard.writeText(integrationCoreStats.gatewayUrl)
    } catch {
      // fallback для окружений без clipboard API
      const ta = document.createElement('textarea')
      ta.value = integrationCoreStats.gatewayUrl
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      ta.remove()
    }
    setCopied(true)
    toast({ title: 'Адрес шлюза скопирован', duration: 2000 })
    window.setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="space-y-6">
      <ViewHeader title="Интеграции" subtitle="Интеграционное ядро — единственная точка связи с интернетом">
        <span className="hidden items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-xs font-semibold text-emerald-700 sm:inline-flex dark:text-emerald-400">
          <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-500" />
          Ядро активно
        </span>
        <Button size="sm" variant="outline" className="gap-1.5 text-xs" onClick={refreshAll}>
          <RefreshCw className="h-3.5 w-3.5" /> Обновить все
        </Button>
      </ViewHeader>

      {/* KPI ядра */}
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <CoreKpi icon={PlugZap} label="Коннекторы" value={`${connectedCount} / ${connectors.length}`} sub="подключено к ядру" />
        <CoreKpi icon={Activity} label="События за 24 ч" value={fmtNum(integrationCoreStats.events24h)} sub="+8% ко вчерашнему дню" />
        <CoreKpi icon={Clock} label="В очереди" value={String(integrationCoreStats.queue)} sub="события в обработке" />
        <CoreKpi icon={ShieldCheck} label="Аптайм ядра" value={integrationCoreStats.uptime} sub="за последние 30 дней" />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        {/* Реестр коннекторов */}
        <Card className="border-zinc-200 shadow-sm dark:border-zinc-800 xl:col-span-2">
          <CardHeader className="flex-row items-center gap-2 space-y-0 pb-4">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-violet-500/10 text-violet-600 dark:text-violet-300">
              <PlugZap className="h-4 w-4" />
            </span>
            <div>
              <CardTitle className="text-base">Реестр коннекторов</CardTitle>
              <CardDescription>Модули не выходят в сеть напрямую — наружу только через ядро</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {connectors.map((c) => {
                const Icon = vendorIcon[c.vendor] ?? Globe
                const m = connectorStatusMap[c.status]
                return (
                  <div
                    key={c.id}
                    className={cn(
                      'group flex flex-col rounded-xl border bg-card p-4 transition-all hover:-translate-y-0.5 hover:shadow-md',
                      c.status === 'error'
                        ? 'border-red-300 dark:border-red-900/60'
                        : 'border-zinc-200 hover:border-emerald-300 dark:border-zinc-800 dark:hover:border-emerald-800'
                    )}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className={cn('flex h-9 w-9 shrink-0 items-center justify-center rounded-lg', m.iconCls)}>
                        <Icon className="h-4 w-4" />
                      </span>
                      <StatusBadge status={c.status} />
                    </div>
                    <p className="mt-2.5 text-sm font-semibold leading-tight">{c.name}</p>
                    <p className="mt-0.5 text-[11px] text-muted-foreground">
                      {c.vendor} · {c.eventsPerDay > 0 ? `${fmtNum(c.eventsPerDay)} событ./день` : 'нет трафика'}
                    </p>
                    <p className="mt-1.5 line-clamp-2 text-xs leading-snug text-muted-foreground">{c.desc}</p>
                    <div className="mt-3 flex items-center justify-between gap-2 border-t border-zinc-100 pt-2.5 dark:border-zinc-800">
                      <span className="flex min-w-0 items-center gap-1 text-[11px] text-muted-foreground">
                        <Clock className="h-3 w-3 shrink-0" />
                        <span className="truncate">{c.lastSync}</span>
                      </span>
                      {c.status === 'connected' && (
                        <Button size="sm" variant="outline" className="h-7 shrink-0 gap-1 px-2 text-[11px]" onClick={() => disconnect(c)}>
                          <Unplug className="h-3 w-3" /> Отключить
                        </Button>
                      )}
                      {c.status === 'error' && (
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 shrink-0 gap-1 border-amber-400 px-2 text-[11px] text-amber-700 hover:bg-amber-50 dark:text-amber-400 dark:hover:bg-amber-950/40"
                          onClick={() => reconnect(c)}
                        >
                          <RefreshCw className="h-3 w-3" /> Переподключить
                        </Button>
                      )}
                      {(c.status === 'available' || c.status === 'off') && (
                        <Button size="sm" className="h-7 shrink-0 gap-1 bg-emerald-600 px-2 text-[11px] text-white hover:bg-emerald-700" onClick={() => connect(c)}>
                          <PlugZap className="h-3 w-3" /> Подключить
                        </Button>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>

        {/* Правая колонка: шлюз + журнал */}
        <div className="space-y-4">
          <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <ShieldCheck className="h-4 w-4 text-emerald-600" /> Единый шлюз
              </CardTitle>
              <CardDescription>Весь внешний трафик — только через ядро</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center gap-2 rounded-lg border border-zinc-200 bg-muted/40 px-3 py-2 dark:border-zinc-700">
                <Globe className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <code className="min-w-0 flex-1 truncate text-xs">{integrationCoreStats.gatewayUrl}</code>
                <button
                  type="button"
                  onClick={copyGateway}
                  aria-label="Скопировать адрес шлюза"
                  title="Скопировать"
                  className="shrink-0 rounded-md p-1 text-muted-foreground transition-colors hover:bg-zinc-200/60 hover:text-foreground dark:hover:bg-zinc-700"
                >
                  {copied ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <Copy className="h-3.5 w-3.5" />}
                </button>
              </div>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div className="rounded-lg bg-muted/50 px-2 py-2">
                  <p className="text-sm font-bold">{integrationCoreStats.uptime}</p>
                  <p className="text-[10px] text-muted-foreground">аптайм</p>
                </div>
                <div className="rounded-lg bg-muted/50 px-2 py-2">
                  <p className="text-sm font-bold">{integrationCoreStats.queue}</p>
                  <p className="text-[10px] text-muted-foreground">в очереди</p>
                </div>
                <div className="rounded-lg bg-muted/50 px-2 py-2">
                  <p className="text-sm font-bold">{integrationCoreStats.trafficPerMin}</p>
                  <p className="text-[10px] text-muted-foreground">трафик/мин</p>
                </div>
              </div>
              <div className="flex items-start gap-2.5 rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2.5 dark:border-amber-900/60 dark:bg-amber-950/30">
                <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
                <p className="text-xs leading-relaxed text-amber-800 dark:text-amber-200">
                  Изоляция контура активна: <b>{integrationCoreStats.blockedExternal} прямых обращений</b> модулей к интернету
                  заблокировано за 24 ч. Наружу — только через ядро.
                </p>
              </div>
            </CardContent>
          </Card>

          <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
            <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
              <div>
                <CardTitle className="flex items-center gap-2 text-base">
                  <Activity className="h-4 w-4 text-emerald-600" /> Журнал трафика
                </CardTitle>
                <CardDescription>События ядра в реальном времени</CardDescription>
              </div>
              <div className="flex items-center gap-2">
                <span
                  className={cn(
                    'hidden items-center gap-1.5 rounded-full px-2 py-0.5 text-[10px] font-bold sm:inline-flex',
                    paused ? 'bg-zinc-500/15 text-muted-foreground' : 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
                  )}
                >
                  <span className={cn('h-1.5 w-1.5 rounded-full', paused ? 'bg-zinc-400' : 'animate-pulse bg-emerald-500')} />
                  {paused ? 'пауза' : 'live'}
                </span>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-7 w-7"
                  onClick={() => setPaused((v) => !v)}
                  aria-label={paused ? 'Возобновить журнал' : 'Поставить журнал на паузу'}
                >
                  {paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
                </Button>
              </div>
            </CardHeader>
            <CardContent className="p-0">
              <div className="max-h-[400px] space-y-0.5 overflow-y-auto px-3 pb-3 erp-scroll" aria-live="off">
                {events.map((ev) => {
                  const Icon = toneIcon[ev.tone]
                  const isBad = ev.status.startsWith('4') || ev.status.startsWith('5')
                  return (
                    <motion.div
                      key={ev.id}
                      initial={{ opacity: 0, y: -6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.25, ease: 'easeOut' }}
                      className="flex items-center gap-2 rounded-md px-2 py-1.5 font-mono text-[11px] leading-snug transition-colors hover:bg-muted/60"
                    >
                      <span className="shrink-0 text-muted-foreground/80">{ev.time}</span>
                      <Icon className={cn('h-3.5 w-3.5 shrink-0', toneCls[ev.tone])} />
                      <span className="min-w-0 flex-1 truncate">
                        <span className="font-semibold">{ev.connector}</span>
                        <span className="text-muted-foreground"> · {ev.action}</span>
                      </span>
                      <span className={cn('shrink-0 font-bold', isBad ? 'text-red-500' : 'text-emerald-600 dark:text-emerald-400')}>
                        {ev.status}
                      </span>
                    </motion.div>
                  )
                })}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      <DraftNote text="Макет: вебхуки маркетплейсов, ретраи очереди, ключи API и журнал ошибок коннекторов." />
    </div>
  )
}

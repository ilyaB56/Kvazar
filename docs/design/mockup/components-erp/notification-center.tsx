'use client'

import { useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { buildNotifications, notificationKindMap, type ErpNotification } from '@/lib/erp-data'
import { Bell, BellOff, CheckCheck, Inbox, ArrowRight } from 'lucide-react'
import { useToast } from '@/hooks/use-toast'
import { AnimatePresence, motion } from 'framer-motion'
import { cn } from '@/lib/utils'

type Filter = 'all' | 'unread'

export function NotificationCenter({ onNavigate }: { onNavigate?: (view: ErpNotification['view']) => void }) {
  // Список собирается из данных ERP: просроченные счета, низкие остатки,
  // переполненные склады, свежие заказы + служебные события
  const [items, setItems] = useState<ErpNotification[]>(() => buildNotifications())
  const [filter, setFilter] = useState<Filter>('all')
  const { toast } = useToast()

  const unreadCount = items.filter((n) => n.unread).length
  const shown = useMemo(
    () => (filter === 'all' ? items : items.filter((n) => n.unread)),
    [items, filter]
  )

  const markRead = (id: string) => {
    setItems((prev) => prev.map((n) => (n.id === id ? { ...n, unread: false } : n)))
  }

  const markAllRead = () => {
    setItems((prev) => prev.map((n) => ({ ...n, unread: false })))
    toast({
      title: 'Все уведомления прочитаны',
      description: `Отмечено: ${unreadCount}`,
      duration: 2500,
    })
  }

  const openNotification = (n: ErpNotification) => {
    markRead(n.id)
    onNavigate?.(n.view)
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="icon" className="relative h-9 w-9" aria-label={`Уведомления${unreadCount ? `, непрочитанных: ${unreadCount}` : ''}`}>
          <Bell className={cn('h-4 w-4 transition-transform', unreadCount > 0 && 'animate-swing')} />
          <AnimatePresence>
            {unreadCount > 0 && (
              <motion.span
                key={unreadCount}
                initial={{ scale: 0.4, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0.4, opacity: 0 }}
                className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white ring-2 ring-background"
              >
                {unreadCount}
              </motion.span>
            )}
          </AnimatePresence>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-[340px] p-0 sm:w-[380px]">
        {/* Шапка */}
        <div className="flex items-center justify-between gap-2 border-b border-zinc-100 px-4 py-3 dark:border-zinc-800">
          <div className="flex items-center gap-2">
            <p className="text-sm font-semibold">Уведомления</p>
            {unreadCount > 0 ? (
              <Badge className="bg-red-500 px-1.5 text-[10px] text-white hover:bg-red-500">
                {unreadCount} новых
              </Badge>
            ) : (
              <Badge variant="secondary" className="text-[10px]">всё прочитано</Badge>
            )}
          </div>
          <button
            onClick={markAllRead}
            disabled={unreadCount === 0}
            className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] font-medium text-emerald-700 transition-colors hover:bg-emerald-50 disabled:cursor-default disabled:text-muted-foreground disabled:hover:bg-transparent dark:text-emerald-400 dark:hover:bg-emerald-950/40 dark:disabled:text-muted-foreground"
          >
            <CheckCheck className="h-3.5 w-3.5" /> Прочитать всё
          </button>
        </div>

        {/* Фильтр */}
        <div className="flex gap-1 border-b border-zinc-100 px-3 py-2 dark:border-zinc-800">
          {([
            { id: 'all' as Filter, label: 'Все', count: items.length },
            { id: 'unread' as Filter, label: 'Непрочитанные', count: unreadCount },
          ]).map((f) => (
            <button
              key={f.id}
              onClick={() => setFilter(f.id)}
              className={cn(
                'flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium transition-colors',
                filter === f.id
                  ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300'
                  : 'text-muted-foreground hover:bg-zinc-100 dark:hover:bg-zinc-800'
              )}
            >
              {f.label}
              <span className={cn(
                'rounded-full px-1 text-[10px] font-bold',
                filter === f.id ? 'bg-emerald-600 text-white' : 'bg-zinc-200 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400'
              )}>
                {f.count}
              </span>
            </button>
          ))}
        </div>

        {/* Список */}
        <div className="max-h-[320px] overflow-y-auto erp-scroll">
          {shown.length === 0 && (
            <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
              <span className="flex h-10 w-10 items-center justify-center rounded-full bg-zinc-100 dark:bg-zinc-800">
                <Inbox className="h-5 w-5 text-muted-foreground" />
              </span>
              <p className="text-sm font-medium">Список пуст</p>
              <p className="flex items-center gap-1 text-xs text-muted-foreground">
                <BellOff className="h-3 w-3" /> Непрочитанных уведомлений нет
              </p>
            </div>
          )}
          {shown.map((n) => {
            const kind = notificationKindMap[n.kind]
            return (
              <button
                key={n.id}
                onClick={() => openNotification(n)}
                className={cn(
                  'group flex w-full items-start gap-3 border-b border-zinc-100 px-4 py-3 text-left transition-colors last:border-0 hover:bg-zinc-50 dark:border-zinc-800/70 dark:hover:bg-zinc-800/50',
                  n.unread && 'bg-emerald-50/50 dark:bg-emerald-950/20'
                )}
              >
                <span className={cn('mt-1.5 h-2 w-2 shrink-0 rounded-full', kind.dot, !n.unread && 'opacity-30')} />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center justify-between gap-2">
                    <span className={cn('truncate text-[13px] leading-snug', n.unread ? 'font-semibold' : 'font-medium text-foreground/80')}>
                      {n.title}
                    </span>
                    <span className="shrink-0 text-[10px] whitespace-nowrap text-muted-foreground">{n.time}</span>
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-muted-foreground">{n.description}</span>
                  <span className="mt-1.5 inline-flex items-center gap-1 text-[11px] font-medium text-emerald-700 opacity-0 transition-opacity group-hover:opacity-100 dark:text-emerald-400">
                    Перейти в раздел <ArrowRight className="h-3 w-3" />
                  </span>
                </span>
              </button>
            )
          })}
        </div>

        {/* Футер */}
        <div className="border-t border-zinc-100 p-1.5 dark:border-zinc-800">
          <Button variant="ghost" size="sm" className="w-full justify-center gap-1.5 text-xs font-medium text-emerald-700 hover:text-emerald-800 dark:text-emerald-400 dark:hover:text-emerald-300">
            Показать все уведомления
          </Button>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

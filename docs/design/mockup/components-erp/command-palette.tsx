'use client'

import { useEffect } from 'react'
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from '@/components/ui/command'
import {
  LayoutDashboard,
  ShoppingCart,
  Boxes,
  Banknote,
  UsersRound,
  FileBarChart2,
  Settings,
  PlugZap,
  Plus,
  FilePlus2,
  UserPlus,
  Truck,
  ClipboardList,
  Search,
  KanbanSquare,
  KeyRound,
  UserCog,
  Share2,
} from 'lucide-react'
import type { ViewId } from './erp-shell'

interface CommandPaletteProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onNavigate: (view: ViewId) => void
  onOpenProfile?: () => void
  onOpenShare?: () => void
}

export function CommandPalette({ open, onOpenChange, onNavigate, onOpenProfile, onOpenShare }: CommandPaletteProps) {
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        onOpenChange(!open)
      }
    }
    document.addEventListener('keydown', down)
    return () => document.removeEventListener('keydown', down)
  }, [open, onOpenChange])

  const go = (view: ViewId) => {
    onNavigate(view)
    onOpenChange(false)
  }

  const sections: { label: string; view: ViewId; icon: React.ReactNode; hint: string }[] = [
    { label: 'Дашборд', view: 'dashboard', icon: <LayoutDashboard />, hint: 'Обзор компании' },
    { label: 'Продажи', view: 'sales', icon: <ShoppingCart />, hint: 'Заказы и воронка сделок' },
    { label: 'Склад', view: 'inventory', icon: <Boxes />, hint: 'Остатки и движения' },
    { label: 'Финансы', view: 'finance', icon: <Banknote />, hint: 'Счета и платежи' },
    { label: 'Персонал', view: 'hr', icon: <UsersRound />, hint: 'Сотрудники и отделы' },
    { label: 'Отчёты', view: 'reports', icon: <FileBarChart2 />, hint: 'Аналитика' },
    { label: 'Интеграции', view: 'integrations', icon: <PlugZap />, hint: 'Ядро и коннекторы' },
    { label: 'Настройки', view: 'settings', icon: <Settings />, hint: 'Параметры системы' },
  ]

  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Глобальный поиск"
      description="Переход между разделами и быстрые действия"
      className="md:min-w-[520px]"
    >
      <CommandInput placeholder="Введите раздел или действие…" />
      <CommandList>
        <CommandEmpty>Ничего не найдено — попробуйте другой запрос.</CommandEmpty>
        <CommandGroup heading="Разделы ERP">
          {sections.map((s) => (
            <CommandItem key={s.view} value={`${s.label} ${s.hint}`} onSelect={() => go(s.view)}>
              <span className="text-emerald-600">{s.icon}</span>
              <span className="flex-1">{s.label}</span>
              <span className="text-xs text-muted-foreground">{s.hint}</span>
            </CommandItem>
          ))}
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading="Быстрые действия">
          <CommandItem value="новый заказ создать" onSelect={() => go('sales')}>
            <Plus className="text-emerald-600" />
            Создать заказ
            <span className="ml-auto text-xs text-muted-foreground">Продажи → Новый заказ</span>
          </CommandItem>
          <CommandItem value="воронка сделок продажи этапы" onSelect={() => go('sales')}>
            <KanbanSquare className="text-emerald-600" />
            Воронка сделок
            <span className="ml-auto text-xs text-muted-foreground">Продажи → Воронка</span>
          </CommandItem>
          <CommandItem value="выставить счёт оплата" onSelect={() => go('finance')}>
            <FilePlus2 className="text-emerald-600" />
            Выставить счёт
            <span className="ml-auto text-xs text-muted-foreground">Финансы → Счета</span>
          </CommandItem>
          <CommandItem value="приёмка товара склад" onSelect={() => go('inventory')}>
            <Truck className="text-emerald-600" />
            Оформить приёмку
            <span className="ml-auto text-xs text-muted-foreground">Склад → Приёмка</span>
          </CommandItem>
          <CommandItem value="нанять сотрудника кадры" onSelect={() => go('hr')}>
            <UserPlus className="text-emerald-600" />
            Нанять сотрудника
            <span className="ml-auto text-xs text-muted-foreground">Персонал → Нанять</span>
          </CommandItem>
          <CommandItem value="мои задачи на неделю" onSelect={() => go('dashboard')}>
            <ClipboardList className="text-emerald-600" />
            Мои задачи
            <span className="ml-auto text-xs text-muted-foreground">Дашборд</span>
          </CommandItem>
          <CommandItem value="конструктор отчёта собрать сформировать" onSelect={() => go('reports')}>
            <FileBarChart2 className="text-emerald-600" />
            Собрать отчёт
            <span className="ml-auto text-xs text-muted-foreground">Отчёты → Конструктор</span>
          </CommandItem>
          <CommandItem value="роли права доступа пользователи" onSelect={() => go('settings')}>
            <KeyRound className="text-emerald-600" />
            Права доступа
            <span className="ml-auto text-xs text-muted-foreground">Настройки → Роли</span>
          </CommandItem>
          <CommandItem value="коннекторы ядро интеграции ozon эдо 1с банк" onSelect={() => go('integrations')}>
            <PlugZap className="text-emerald-600" />
            Журнал трафика ядра
            <span className="ml-auto text-xs text-muted-foreground">Интеграции → Коннекторы</span>
          </CommandItem>
          <CommandItem
            value="профиль роли мой аккаунт сессии безопасность"
            onSelect={() => {
              onOpenChange(false)
              onOpenProfile?.()
            }}
          >
            <UserCog className="text-emerald-600" />
            Профиль и роли
            <span className="ml-auto text-xs text-muted-foreground">Аккаунт · сессии · 2FA</span>
          </CommandItem>
          <CommandItem
            value="поделиться ссылкой отправить коллеге скачать презентацию pdf zip"
            onSelect={() => {
              onOpenChange(false)
              onOpenShare?.()
            }}
          >
            <Share2 className="text-emerald-600" />
            Поделиться макетом
            <span className="ml-auto text-xs text-muted-foreground">Ссылка · PDF · ZIP</span>
          </CommandItem>
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading="Подсказка">
          <CommandItem disabled value="подсказка поиск">
            <Search className="text-muted-foreground" />
            <span className="text-xs text-muted-foreground">
              Ctrl+K — открыть поиск в любом месте системы
            </span>
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  )
}

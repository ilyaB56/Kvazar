'use client'

import { useState, useSyncExternalStore } from 'react'
import Image from 'next/image'
import { useTheme } from 'next-themes'
import { motion } from 'framer-motion'
import { Button } from '@/components/ui/button'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Separator } from '@/components/ui/separator'
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from '@/components/ui/sheet'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import { DashboardView } from './dashboard-view'
import { SalesView } from './sales-view'
import { InventoryView } from './inventory-view'
import { FinanceView } from './finance-view'
import { HrView } from './hr-view'
import { ReportsView } from './reports-view'
import { SettingsView } from './settings-view'
import { IntegrationsView } from './integrations-view'
import {
  LayoutDashboard,
  ShoppingCart,
  Boxes,
  Banknote,
  UsersRound,
  FileBarChart2,
  Settings,
  Search,
  Menu,
  HelpCircle,
  ChevronsUpDown,
  LogOut,
  UserCog,
  Sun,
  Moon,
  PenTool,
  Share2,
  PlugZap,
} from 'lucide-react'
import { CommandPalette } from './command-palette'
import { NotificationCenter } from './notification-center'
import { UserProfileDialog } from './user-profile-dialog'
import { ShareDialog } from './share-dialog'
import type { ErpNotification } from '@/lib/erp-data'

type ViewId = 'dashboard' | 'sales' | 'inventory' | 'finance' | 'hr' | 'reports' | 'integrations' | 'settings'

interface NavItem {
  id: ViewId
  label: string
  icon: React.ReactNode
  badge?: string
  badgeTone?: string
}

const navSections: { title: string; items: NavItem[] }[] = [
  {
    title: 'Оперативная работа',
    items: [
      { id: 'dashboard', label: 'Дашборд', icon: <LayoutDashboard className="h-4 w-4" /> },
      { id: 'sales', label: 'Продажи', icon: <ShoppingCart className="h-4 w-4" />, badge: '46', badgeTone: 'bg-emerald-500' },
      { id: 'inventory', label: 'Склад', icon: <Boxes className="h-4 w-4" />, badge: '3', badgeTone: 'bg-red-500' },
    ],
  },
  {
    title: 'Области учёта',
    items: [
      { id: 'finance', label: 'Финансы', icon: <Banknote className="h-4 w-4" />, badge: '1', badgeTone: 'bg-amber-500' },
      { id: 'hr', label: 'Персонал', icon: <UsersRound className="h-4 w-4" /> },
      { id: 'reports', label: 'Отчёты', icon: <FileBarChart2 className="h-4 w-4" /> },
    ],
  },
  {
    title: 'Система',
    items: [
      { id: 'integrations', label: 'Интеграции', icon: <PlugZap className="h-4 w-4" />, badge: '1', badgeTone: 'bg-red-500' },
      { id: 'settings', label: 'Настройки', icon: <Settings className="h-4 w-4" /> },
    ],
  },
]

const viewTitles: Record<ViewId, { title: string; crumb: string }> = {
  dashboard: { title: 'Дашборд', crumb: 'Обзор компании' },
  sales: { title: 'Продажи', crumb: 'Заказы клиентов' },
  inventory: { title: 'Склад', crumb: 'Остатки и движения' },
  finance: { title: 'Финансы', crumb: 'Счета и платежи' },
  hr: { title: 'Персонал', crumb: 'Сотрудники и отделы' },
  reports: { title: 'Отчёты', crumb: 'Аналитика' },
  integrations: { title: 'Интеграции', crumb: 'Интеграционное ядро' },
  settings: { title: 'Настройки', crumb: 'Параметры системы' },
}

function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="relative block h-9 w-9 shrink-0 overflow-hidden rounded-lg bg-zinc-950 shadow-sm ring-1 ring-white/15">
        <Image
          src="/brand/quasar-violet.png"
          alt="Логотип ERP «Квазар»: светлое ядро в орбитальных кольцах"
          fill
          sizes="36px"
          className="object-cover"
        />
      </span>
      {!compact && (
        <div className="leading-tight">
          <p className="text-sm font-bold tracking-tight text-white">Квазар</p>
          <p className="text-[11px] text-zinc-400">ERP · макет v0.11</p>
        </div>
      )}
    </div>
  )
}

function SidebarNav({ current, onNavigate }: { current: ViewId; onNavigate: (v: ViewId) => void }) {
  return (
    <nav className="min-h-0 flex-1 space-y-4 overflow-y-auto px-3 py-3 erp-scroll" aria-label="Основная навигация">
      {navSections.map((section) => (
        <div key={section.title}>
          <p className="mb-1.5 px-3 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
            {section.title}
          </p>
          <div className="space-y-0.5">
            {section.items.map((item) => {
              const active = current === item.id
              return (
                <button
                  key={item.id}
                  onClick={() => onNavigate(item.id)}
                  aria-current={active ? 'page' : undefined}
                  className={cn(
                    'group flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                    active
                      ? 'bg-emerald-500/15 text-emerald-300'
                      : 'text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-100'
                  )}
                >
                  <span className={cn('transition-colors', active ? 'text-emerald-400' : 'text-zinc-500 group-hover:text-zinc-300')}>
                    {item.icon}
                  </span>
                  <span className="flex-1 text-left">{item.label}</span>
                  {item.badge && (
                    <span className={cn(
                      'flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-[11px] font-bold text-white',
                      item.badgeTone
                    )}>
                      {item.badge}
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        </div>
      ))}
    </nav>
  )
}

function SidebarFooter() {
  return (
    <div className="border-t border-zinc-800 p-3">
      <div className="rounded-xl bg-gradient-to-br from-zinc-800 to-zinc-900 p-3">
        <p className="flex items-center gap-1.5 text-xs font-semibold text-zinc-100">
          <PenTool className="h-3.5 w-3.5 shrink-0 text-emerald-400" /> Это макет интерфейса
        </p>
        <p className="mt-1 text-[11px] leading-snug text-zinc-400">
          Набросок ERP: демо-данные и нереальные суммы.
        </p>
      </div>
    </div>
  )
}

export function ErpShell({ onLogout }: { onLogout?: () => void }) {
  const [view, setView] = useState<ViewId>('dashboard')
  const [mobileOpen, setMobileOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [profileOpen, setProfileOpen] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)
  const { theme, setTheme } = useTheme()

  // hydration-safe флаг «смонтировано на клиенте» (без setState в эффекте)
  const emptySubscribe = () => () => {}
  const mounted = useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false
  )

  const navigate = (v: ViewId) => {
    setView(v)
    setMobileOpen(false)
  }

  const logout = () => {
    try {
      window.localStorage.removeItem('erp-demo-auth')
    } catch {
      /* noop */
    }
    onLogout?.()
  }

  const renderView = () => {
    switch (view) {
      case 'dashboard': return <DashboardView onOpenFinance={() => navigate('finance')} />
      case 'sales': return <SalesView />
      case 'inventory': return <InventoryView />
      case 'finance': return <FinanceView />
      case 'hr': return <HrView />
      case 'reports': return <ReportsView />
      case 'integrations': return <IntegrationsView />
      case 'settings': return <SettingsView />
    }
  }

  return (
    <div className="flex min-h-screen bg-background">
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        onNavigate={navigate}
        onOpenProfile={() => setProfileOpen(true)}
        onOpenShare={() => setShareOpen(true)}
      />
      <UserProfileDialog open={profileOpen} onOpenChange={setProfileOpen} onLogout={logout} />
      <ShareDialog open={shareOpen} onOpenChange={setShareOpen} />
      {/* Десктопный сайдбар */}
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-64 flex-col bg-zinc-900 lg:flex print:hidden">
        <div className="flex h-16 items-center border-b border-zinc-800 px-5">
          <Logo />
        </div>
        <SidebarNav current={view} onNavigate={navigate} />
        <SidebarFooter />
      </aside>

      {/* Основная область */}
      <div className="flex min-h-screen w-full flex-col lg:pl-64">
        {/* Шапка */}
        <header className="sticky top-0 z-30 flex h-16 items-center gap-2 border-b border-zinc-200 bg-background/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/80 sm:gap-3 sm:px-6 dark:border-zinc-800 print:hidden">
          {/* Мобильное меню */}
          <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
            <SheetTrigger asChild>
              <Button variant="outline" size="icon" className="h-9 w-9 lg:hidden" aria-label="Открыть меню">
                <Menu className="h-4 w-4" />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-72 border-0 bg-zinc-900 p-0">
              <SheetTitle className="sr-only">Навигация по разделам</SheetTitle>
              <div className="flex h-16 items-center border-b border-zinc-800 px-5">
                <Logo />
              </div>
              <SidebarNav current={view} onNavigate={navigate} />
              <SidebarFooter />
            </SheetContent>
          </Sheet>

          {/* Хлебные крошки / заголовок */}
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-foreground">
              {viewTitles[view].title}
              <span className="ml-2 hidden font-normal text-muted-foreground sm:inline">· {viewTitles[view].crumb}</span>
            </p>
          </div>

          {/* Поиск (открывает палитру Ctrl+K) */}
          <button
            onClick={() => setPaletteOpen(true)}
            className="hidden h-9 w-56 items-center gap-2 rounded-full border border-transparent bg-zinc-100 px-3 text-sm text-muted-foreground transition-colors hover:border-zinc-200 hover:bg-zinc-50 lg:flex lg:w-64 dark:bg-zinc-800/70 dark:hover:bg-zinc-800 dark:hover:border-zinc-700"
            aria-label="Открыть поиск (Ctrl+K)"
          >
            <Search className="h-4 w-4 shrink-0" />
            <span className="flex-1 text-left">Поиск по ERP…</span>
            <kbd className="rounded border border-zinc-200 bg-white px-1.5 py-0.5 text-[10px] font-semibold text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400">
              Ctrl K
            </kbd>
          </button>
          <Button
            variant="outline"
            size="icon"
            className="h-9 w-9 md:hidden"
            onClick={() => setPaletteOpen(true)}
            aria-label="Поиск"
          >
            <Search className="h-4 w-4" />
          </Button>

          {/* Переключатель темы */}
          <Button
            variant="outline"
            size="icon"
            className="h-9 w-9"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            aria-label={mounted && theme === 'dark' ? 'Включить светлую тему' : 'Включить тёмную тему'}
            title={mounted && theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'}
          >
            {mounted && theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>

          {/* Поделиться макетом */}
          <Button
            variant="outline"
            size="icon"
            className="h-9 w-9"
            onClick={() => setShareOpen(true)}
            aria-label="Поделиться макетом"
            title="Поделиться макетом"
          >
            <Share2 className="h-4 w-4" />
          </Button>

          {/* Центр уведомлений */}
          <NotificationCenter
            onNavigate={(v: ErpNotification['view']) => navigate(v)}
          />

          {/* Помощь */}
          <Button variant="outline" size="icon" className="hidden h-9 w-9 sm:inline-flex" aria-label="Помощь">
            <HelpCircle className="h-4 w-4" />
          </Button>

          {/* Профиль */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                className="flex items-center gap-2 rounded-full border border-zinc-200 bg-white py-1 pl-1 pr-2.5 transition-colors hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:hover:bg-zinc-800"
                aria-label="Меню пользователя"
              >
                <Avatar className="h-7 w-7">
                  <AvatarFallback className="bg-emerald-100 text-xs font-bold text-emerald-700">ВН</AvatarFallback>
                </Avatar>
                <span className="hidden text-sm font-medium sm:inline">В. Науменко</span>
                <ChevronsUpDown className="h-3.5 w-3.5 text-muted-foreground" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-52">
              <DropdownMenuLabel>
                <p>Науменко Владимир</p>
                <p className="text-xs font-normal text-muted-foreground">v.naumenko@technoprom.ru</p>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => setPaletteOpen(true)}>
                <Search className="mr-2 h-4 w-4" /> Поиск (Ctrl+K)
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              >
                {mounted && theme === 'dark' ? (
                  <><Sun className="mr-2 h-4 w-4" /> Светлая тема</>
                ) : (
                  <><Moon className="mr-2 h-4 w-4" /> Тёмная тема</>
                )}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setProfileOpen(true)}>
                <UserCog className="mr-2 h-4 w-4" /> Профиль и роли
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setShareOpen(true)}>
                <Share2 className="mr-2 h-4 w-4" /> Поделиться макетом
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem className="text-red-600 focus:text-red-600" onClick={logout}>
                <LogOut className="mr-2 h-4 w-4" /> Выйти
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </header>

        {/* Контент */}
        <main className="flex-1 px-4 py-6 sm:px-6 lg:px-8">
          <div className="mx-auto w-full max-w-[1400px]">
            <motion.div
              key={view}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.22, ease: 'easeOut' }}
            >
              {renderView()}
            </motion.div>
          </div>
        </main>

        {/* Липкий футер */}
        <footer className="mt-auto border-t border-zinc-200 bg-background px-4 py-4 sm:px-6 lg:px-8 dark:border-zinc-800">
          <div className="mx-auto flex w-full max-w-[1400px] flex-col items-center justify-between gap-2 text-xs text-muted-foreground sm:flex-row">
            <p>
              © 2025 ERP «Квазар» · макет интерфейса · <span className="font-medium text-emerald-700 dark:text-emerald-400">демо-данные ООО «ТехноПром»</span>
            </p>
            <div className="flex items-center gap-4">
              <span>Сборка v0.11.0</span>
              <Separator orientation="vertical" className="hidden h-3 sm:block" />
              <span>БД: 12 483 записи</span>
              <Separator orientation="vertical" className="hidden h-3 sm:block" />
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-500" /> Все сервисы работают
              </span>
            </div>
          </div>
        </footer>
      </div>
    </div>
  )
}

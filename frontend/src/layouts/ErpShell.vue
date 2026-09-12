<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import {
  Menu, Sun, Moon, LogOut, KeyRound, ChevronsUpDown, ShieldAlert, Plug, Bot, Settings2, LayoutDashboard, Wallet, TrendingUp,
  BarChart3, Package, Truck,
} from 'lucide-vue-next'
import { get, post } from '../api/client'
import { useAuthStore } from '../stores/auth'
import QuasarMark from '../components/brand/QuasarMark.vue'
import NotificationCenter from './NotificationCenter.vue'
import { Avatar, Button, Dialog, DropdownMenu, DropdownMenuItem, Input, Label, ToastHost, useToast } from '../components/ui'
import ChangePasswordDialog from './ChangePasswordDialog.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const toast = useToast()

// ---------- Тема (§3: .dark на <html>, localStorage, 0.25s) ----------
const THEME_KEY = 'erp-theme'
const theme = ref<'light' | 'dark'>(
  (localStorage.getItem(THEME_KEY) as 'light' | 'dark') ?? 'light',
)
watch(theme, (value) => {
  document.documentElement.classList.toggle('dark', value === 'dark')
  localStorage.setItem(THEME_KEY, value)
}, { immediate: true })
function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
}

// ---------- Навигация (§5; фильтрация по правам — этап C) ----------
// В этапе A показываем только живые разделы; Дашборд/Продажи/Финансы/Отчёты
// включаются своими этапами (D/E/F), склад/персонал — после бэкенда (ADR-007).
// Пункт виден, если уровень прав на модуль ≠ none (§6.3); Настройки — админ
// (system-модуль переводится на require_module в этапе G).
interface NavItem {
  to: string
  label: string
  icon: typeof Plug
}
const navSections = computed(() => [
  {
    title: t('nav.sections.operations'),
    items: ([
      { to: '/dashboard', label: t('nav.dashboard'), icon: LayoutDashboard },
    ] satisfies Array<NavItem>),
  },
  {
    title: t('nav.sections.domains'),
    items: ([
      auth.moduleLevel('accounting') !== 'none'
        ? { to: '/accounting', label: t('nav.finance'), icon: Wallet }
        : null,
      auth.moduleLevel('accounting') !== 'none'
        ? { to: '/purchasing', label: t('nav.purchasing'), icon: Truck }
        : null,
      auth.moduleLevel('accounting') !== 'none'
        ? { to: '/inventory', label: t('nav.inventory'), icon: Package }
        : null,
      auth.moduleLevel('crm') !== 'none'
        ? { to: '/crm', label: t('nav.sales'), icon: TrendingUp }
        : null,
      auth.moduleLevel('accounting') !== 'none' || auth.moduleLevel('crm') !== 'none'
        ? { to: '/reports', label: t('nav.reports'), icon: BarChart3 }
        : null,
      auth.moduleLevel('integrations') !== 'none'
        ? { to: '/integrations/connections', label: t('nav.integrations'), icon: Plug }
        : null,
      auth.moduleLevel('ai') !== 'none'
        ? { to: '/assistant', label: t('nav.assistant'), icon: Bot }
        : null,
      { to: '/settings/organization', label: t('nav.settings'), icon: Settings2 },
    ] as Array<NavItem | null>).filter((item): item is NavItem => item !== null),
  },
].filter((section) => section.items.length > 0))

// заголовок/крошка — по meta.titleKey/crumbKey (i18n, этап 1.1)
const pageTitle = computed(() => route.meta.titleKey ? t(route.meta.titleKey as string) : '')
const pageCrumb = computed(() => route.meta.crumbKey ? t(route.meta.crumbKey as string) : '')

function isActive(to: string): boolean {
  return route.path === to || route.path.startsWith(`${to}/`)
}

// ---------- Организация (подпись под логотипом, §5) ----------
const orgName = ref('')
onMounted(async () => {
  try {
    const companies = await get<Array<{ name: string }>>('/companies')
    orgName.value = companies[0]?.name ?? ''
  } catch {
    orgName.value = ''
  }
})

// ---------- Мобильный drawer (§5, < lg) ----------
const mobileOpen = ref(false)
watch(() => route.path, () => { mobileOpen.value = false })

// ---------- Футер: версия + /health (§5) ----------
const health = ref<'ok' | 'fail' | 'pending'>('pending')
let healthTimer: number | null = null
async function checkHealth() {
  try {
    const response = await fetch('/health')
    health.value = response.ok ? 'ok' : 'fail'
  } catch {
    health.value = 'fail'
  }
}
onMounted(() => {
  checkHealth()
  healthTimer = window.setInterval(checkHealth, 60_000)
})
onBeforeUnmount(() => { if (healthTimer !== null) window.clearInterval(healthTimer) })

// ---------- Права: опрос раз в минуту (§6.3: смена прав применяется
// в течение минуты без перелогина — навигация перестраивается сама) ----------
let permissionsTimer: number | null = null
onMounted(() => {
  void auth.fetchPermissions()
  permissionsTimer = window.setInterval(() => { void auth.fetchPermissions() }, 60_000)
})
onBeforeUnmount(() => { if (permissionsTimer !== null) window.clearInterval(permissionsTimer) })

// ---------- Профиль: смена пароля + выход ----------
const passwordOpen = ref(false)

// ---------- Модалка «в аккаунт уже вошли» (sessions-security §2.3) ----------
// Показ один раз за вход (activeSessions>1), интерфейс не блокирует:
// «Завершить другие сеансы» или «Продолжить» — оба живут дальше.
const sessionWarn = ref(false)
const sessionBusy = ref(false)
// шаг 2 — подтверждение паролем (спека §2.3): ошибки инлайн, окно живо
const sessionStep = ref<'info' | 'password'>('info')
const sessionPassword = ref('')
const sessionError = ref('')
watch(
  () => [auth.isAuthenticated, auth.activeSessions] as const,
  ([authenticated, count]) => { sessionWarn.value = authenticated && count > 1 },
  { immediate: true },
)
watch(sessionWarn, (open) => {
  if (open) {
    sessionStep.value = 'info'
    sessionPassword.value = ''
    sessionError.value = ''
  }
})
async function terminateOtherSessions() {
  if (sessionBusy.value || !sessionPassword.value) return
  sessionBusy.value = true
  sessionError.value = ''
  try {
    const terminated = await auth.logoutOthers(sessionPassword.value)
    toast.success(t('sessions.terminatedToast', { n: terminated }))
    sessionWarn.value = false
  } catch (error) {
    const { status, retryAfter } = error as { status?: number; retryAfter?: number }
    if (status === 403) sessionError.value = t('sessions.wrongPassword')
    else if (status === 429) {
      sessionError.value = t('sessions.rateLimited', { n: retryAfter ?? 60 })
    } else {
      sessionError.value = (error as Error).message
    }
  } finally {
    sessionBusy.value = false
  }
}
async function logout() {
  await auth.apiLogout()
  auth.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <div class="flex min-h-screen bg-background">
    <!-- Сайдбар: десктоп -->
    <aside class="fixed inset-y-0 left-0 z-40 hidden w-64 flex-col bg-zinc-900 lg:flex print:hidden">
      <div class="flex h-16 shrink-0 items-center border-b border-zinc-800 px-5">
        <div class="flex items-center gap-2.5">
          <span class="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-zinc-950 shadow-sm ring-1 ring-white/15">
            <QuasarMark class="h-7 w-7" />
          </span>
          <div class="leading-tight">
            <p class="text-sm font-bold tracking-tight text-white">{{ t('brand.name') }}</p>
            <p class="truncate text-[11px] text-zinc-400" :title="orgName">
              {{ orgName || t('brand.tagline') }}
            </p>
          </div>
        </div>
      </div>
      <nav class="min-h-0 flex-1 space-y-4 overflow-y-auto px-3 py-3 erp-scroll" aria-label="Основная навигация">
        <div v-for="section in navSections" :key="section.title">
          <p class="mb-1.5 px-3 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">{{ section.title }}</p>
          <div class="space-y-0.5">
            <router-link
              v-for="item in section.items"
              :key="item.to"
              :to="item.to"
              :aria-current="isActive(item.to) ? 'page' : undefined"
              class="group flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors"
              :class="isActive(item.to)
                ? 'bg-emerald-500/15 text-emerald-300'
                : 'text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-100'"
            >
              <component :is="item.icon" class="h-4 w-4 shrink-0" :class="isActive(item.to) ? 'text-emerald-400' : 'text-zinc-500 group-hover:text-zinc-300'" />
              <span class="flex-1 text-left">{{ item.label }}</span>
            </router-link>
          </div>
        </div>
      </nav>
      <div class="border-t border-zinc-800 p-3 text-[11px] leading-snug text-zinc-500">
        {{ t('shell.sidebarNote') }}
      </div>
    </aside>

    <!-- Сайдбар: мобильный drawer -->
    <Teleport to="body">
      <div v-if="mobileOpen" class="fixed inset-0 z-50 lg:hidden">
        <div class="absolute inset-0 bg-black/50" @click="mobileOpen = false" />
        <aside class="absolute inset-y-0 left-0 flex w-72 flex-col bg-zinc-900">
          <div class="flex h-16 items-center border-b border-zinc-800 px-5">
            <div class="flex items-center gap-2.5">
              <span class="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-zinc-950 shadow-sm ring-1 ring-white/15">
                <QuasarMark class="h-7 w-7" />
              </span>
              <div class="leading-tight">
                <p class="text-sm font-bold text-white">{{ t('brand.name') }}</p>
                <p class="truncate text-[11px] text-zinc-400 max-w-40">{{ orgName || t('brand.tagline') }}</p>
              </div>
            </div>
          </div>
          <nav class="flex-1 overflow-y-auto px-3 py-3 erp-scroll" aria-label="Мобильная навигация">
            <div v-for="section in navSections" :key="section.title" class="mb-4">
              <p class="mb-1.5 px-3 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">{{ section.title }}</p>
              <router-link
                v-for="item in section.items"
                :key="item.to"
                :to="item.to"
                class="group flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium"
                :class="isActive(item.to) ? 'bg-emerald-500/15 text-emerald-300' : 'text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-100'"
              >
                <component :is="item.icon" class="h-4 w-4" />
                {{ item.label }}
              </router-link>
            </div>
          </nav>
        </aside>
      </div>
    </Teleport>

    <!-- Основная область -->
    <div class="flex min-h-screen w-full flex-col lg:pl-64">
      <!-- Шапка -->
      <header class="sticky top-0 z-30 flex h-16 items-center gap-2 border-b border-zinc-200 bg-background/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/80 sm:gap-3 sm:px-6 dark:border-zinc-800 print:hidden">
        <Button variant="outline" size="icon" class="h-9 w-9 lg:hidden" :aria-label="t('shell.openMenu')" @click="mobileOpen = true">
          <Menu class="h-4 w-4" />
        </Button>
        <div class="min-w-0 flex-1">
          <p class="truncate text-sm font-semibold text-foreground">
            {{ pageTitle }}
            <span v-if="pageCrumb" class="ml-2 hidden font-normal text-muted-foreground sm:inline">· {{ pageCrumb }}</span>
          </p>
        </div>
        <Button variant="outline" size="icon" class="h-9 w-9" :aria-label="theme === 'dark' ? t('shell.themeLight') : t('shell.themeDark')" @click="toggleTheme">
          <Sun v-if="theme === 'dark'" class="h-4 w-4" />
          <Moon v-else class="h-4 w-4" />
        </Button>
        <NotificationCenter />
      <!-- гейт 1.4: seed-админ ещё не сменил пароль -->
      <div
        v-if="auth.user?.must_change_password"
        class="flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300"
      >
        <ShieldAlert class="h-3.5 w-3.5 shrink-0" />
        {{ t('shell.changeSeedPassword') }}
        <button type="button" class="ml-auto font-semibold underline" @click="passwordOpen = true">
          {{ t('password.submit') }}
        </button>
      </div>
        <DropdownMenu align="end">
          <template #trigger>
            <span class="flex items-center gap-2 rounded-full border border-zinc-200 bg-card py-1 pl-1 pr-2.5 transition-colors hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:hover:bg-zinc-800" :aria-label="t('shell.userMenu')">
              <Avatar :initials="auth.userName.slice(0, 2).toUpperCase()" />
              <span class="hidden max-w-32 truncate text-sm font-medium sm:inline">{{ auth.userName }}</span>
              <ChevronsUpDown class="h-3.5 w-3.5 text-muted-foreground" />
            </span>
          </template>
          <template #label>
            <p class="font-medium">{{ auth.userName }}</p>
            <p class="text-xs font-normal text-muted-foreground">{{ t(`roles.${auth.user?.role ?? 'user'}`) }}</p>
          </template>
          <DropdownMenuItem @click="passwordOpen = true">
            <KeyRound class="h-4 w-4" /> {{ t('shell.changePassword') }}
          </DropdownMenuItem>
          <DropdownMenuItem destructive @click="logout">
            <LogOut class="h-4 w-4" /> {{ t('shell.logout') }}
          </DropdownMenuItem>
        </DropdownMenu>
      </header>

      <!-- Контент (появление экрана 0.22s) -->
      <main class="flex-1 px-4 py-6 sm:px-6 lg:px-8">
        <div class="mx-auto w-full max-w-[1400px]">
          <router-view :key="route.path" class="erp-view-in" />
        </div>
      </main>

      <!-- Футер: версия + статус сервисов -->
      <footer class="mt-auto border-t border-zinc-200 bg-background px-4 py-4 sm:px-6 lg:px-8 dark:border-zinc-800 print:hidden">
        <div class="mx-auto flex w-full max-w-[1400px] flex-col items-center justify-between gap-2 text-xs text-muted-foreground sm:flex-row">
          <p>{{ t('brand.name') }} · {{ t('shell.copyright') }}</p>
          <div class="flex items-center gap-4">
            <span>{{ t('shell.build') }} v0.2.0</span>
            <span class="flex items-center gap-1.5">
              <span class="h-2 w-2 animate-pulse rounded-full" :class="health === 'ok' ? 'bg-emerald-500' : health === 'fail' ? 'bg-red-500' : 'bg-zinc-400'" />
              {{ health === 'ok' ? t('shell.servicesOk') : health === 'fail' ? t('shell.servicesFail') : t('shell.servicesPending') }}
            </span>
          </div>
        </div>
      </footer>
    </div>

    <ChangePasswordDialog v-model="passwordOpen" />

    <!-- В аккаунт уже выполнен вход: выбор пользователя, не блокировка;
         завершение — с подтверждением паролем (спека §2.3) -->
    <Dialog :open="sessionWarn" :title="t('sessions.warnTitle')" width="480px"
            @update:open="(v: boolean) => { if (!v) sessionWarn = false }">
      <div v-if="sessionStep === 'info'" class="space-y-4">
        <div class="flex items-start gap-3">
          <span class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-amber-100 text-amber-600 dark:bg-amber-950/60 dark:text-amber-400">
            <ShieldAlert class="h-5 w-5" />
          </span>
          <p class="text-sm leading-relaxed text-muted-foreground">
            {{ t('sessions.warnText', { n: auth.activeSessions - 1 }) }}
          </p>
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="sessionWarn = false">
            {{ t('sessions.warnContinue') }}
          </Button>
          <Button variant="emerald" size="sm" @click="sessionStep = 'password'">
            {{ t('sessions.warnTerminate') }}
          </Button>
        </div>
      </div>
      <form v-else class="space-y-4" @submit.prevent="terminateOtherSessions">
        <p class="text-sm leading-relaxed text-muted-foreground">
          {{ t('sessions.passwordPrompt') }}
        </p>
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('sessions.passwordLabel') }}</Label>
          <Input
            v-model="sessionPassword" type="password"
            :placeholder="'••••••••'" autofocus
          />
        </div>
        <p v-if="sessionError" class="text-xs font-medium text-red-600 dark:text-red-400">
          {{ sessionError }}
        </p>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" type="button" @click="sessionStep = 'info'">
            {{ t('sessions.backButton') }}
          </Button>
          <Button variant="emerald" size="sm" type="submit"
                  :disabled="sessionBusy || !sessionPassword">
            {{ t('sessions.confirmButton') }}
          </Button>
        </div>
      </form>
    </Dialog>
    <ToastHost />
  </div>
</template>

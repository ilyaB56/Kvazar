<script setup lang="ts">
// Центр уведомлений v1-lite (редизайн-спека §5.1): точная копия структуры
// notification-center.tsx эталона, но наполняется ТОЛЬКО реальными
// событиями системы — демо-данных нет. Источники v1:
//   sync-runs за 24 ч (red) → Интеграции → Синхронизации
//   бэкапы: нет свежее 25 ч / ошибка (amber) → Настройки (админ)
//   непроверенные предложения ИИ (sky) → Ассистент
// Прочитанность — localStorage, ключ `тип:entity_id:дата` (на следующий
// день служебное событие снова сигнализирует). Серверная персистентность
// и бизнес-события — этап H.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { ArrowRight, Bell, BellOff, CheckCheck, Inbox } from 'lucide-vue-next'
import { get } from '../api/client'
import { Badge, Button } from '../components/ui'
import { cn } from '../lib/utils'

type ItemKind = 'red' | 'amber' | 'sky'

interface NotifyItem {
  id: string // stable: тип:entity_id:дата
  kind: ItemKind
  title: string
  description: string
  time: string
  to: string
}

const READ_PREFIX = 'erp-read:'

const { t, d } = useI18n()
const router = useRouter()

const open = ref(false)
const filter = ref<'all' | 'unread'>('all')
const items = ref<NotifyItem[]>([])
const root = ref<HTMLElement | null>(null)

// прочитанность в localStorage — вне реактивности Vue, поэтому компьютеры
// зависят от этого счётчика: markRead поднимает его и форсит пересчёт
const readTick = ref(0)

const unread = computed(() => {
  void readTick.value
  return items.value.filter((item) => !isRead(item))
})
const shown = computed(() => {
  void readTick.value
  return filter.value === 'all' ? items.value : items.value.filter((item) => !isRead(item))
})

function isRead(item: NotifyItem): boolean {
  try {
    return localStorage.getItem(READ_PREFIX + item.id) === '1'
  } catch {
    return false
  }
}

function markRead(item: NotifyItem) {
  try {
    localStorage.setItem(READ_PREFIX + item.id, '1')
  } catch {
    /* noop — приватный режим */
  }
  readTick.value++
}

function markAllRead() {
  for (const item of unread.value) markRead(item)
}

function openItem(item: NotifyItem) {
  markRead(item)
  open.value = false
  router.push(item.to)
}

function onDocClick(event: MouseEvent) {
  if (open.value && root.value && !root.value.contains(event.target as Node)) open.value = false
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') open.value = false
}

let refreshTimer: number | null = null

// ---------- Источники (все существуют; 403 = раздел не наш — пропускаем) ----------

async function syncErrors(): Promise<NotifyItem[]> {
  const out: NotifyItem[] = []
  let jobs: Array<{ id: string; name: string; is_active: boolean }> = []
  try {
    jobs = await get('/integrations/sync-jobs')
  } catch {
    return out
  }
  const dayAgo = Date.now() - 24 * 3600 * 1000
  for (const job of jobs.filter((j) => j.is_active)) {
    try {
      const runs = await get<Array<{
        id: number; status: string; error: string; started_at: string
      }>>(`/integrations/sync-runs?sync_job_id=${job.id}`)
      const failed = runs.find(
        (run) => run.status === 'error' && Date.parse(run.started_at) >= dayAgo,
      )
      if (failed) {
        const day = failed.started_at.slice(0, 10)
        out.push({
          id: `sync_error:${failed.id}:${day}`,
          kind: 'red',
          title: t('notify.syncErrorTitle', { job: job.name }),
          description: (failed.error || '').slice(0, 120) || t('notify.syncErrorDesc'),
          time: d(new Date(failed.started_at), 'short'),
          to: '/integrations/sync',
        })
      }
    } catch {
      /* задание недоступно — пропускаем */
    }
  }
  return out
}

async function backupAlert(): Promise<NotifyItem[]> {
  let backups: Array<{ id: string; status: string; created_at: string }> = []
  try {
    backups = await get('/system/backups')
  } catch {
    return [] // не админ или раздел недоступен
  }
  const day = new Date().toISOString().slice(0, 10)
  const latest = backups[0]
  const stale = !latest || Date.parse(latest.created_at) < Date.now() - 25 * 3600 * 1000
  const failed = backups.some(
    (backup) => backup.status === 'error'
      && Date.parse(backup.created_at) >= Date.now() - 25 * 3600 * 1000,
  )
  if (!stale && !failed) return []
  return [{
    id: `backup_stale:${latest?.id ?? 'none'}:${day}`,
    kind: 'amber',
    title: failed ? t('notify.backupFailedTitle') : t('notify.backupStaleTitle'),
    description: latest ? d(new Date(latest.created_at), 'long') : t('notify.backupNone'),
    time: latest ? d(new Date(latest.created_at), 'short') : '',
    to: '/settings/system',
  }]
}

async function aiProposals(): Promise<NotifyItem[]> {
  let proposals: Array<{ id: string; created_at: string }> = []
  try {
    proposals = await get('/ai/proposals?status=pending')
  } catch {
    return []
  }
  if (!proposals.length) return []
  const newest = proposals[0]
  return [{
    id: `ai_proposals:${newest.id}:${newest.created_at.slice(0, 10)}`,
    kind: 'sky',
    title: t('notify.aiTitle', { count: proposals.length }),
    description: t('notify.aiDesc'),
    time: d(new Date(newest.created_at), 'short'),
    to: '/assistant',
  }]
}

async function refresh() {
  const [sync, backup, ai] = await Promise.all([
    syncErrors(), backupAlert(), aiProposals(),
  ])
  items.value = [...sync, ...backup, ...ai]
}

onMounted(() => {
  refresh()
  refreshTimer = window.setInterval(refresh, 60_000)
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  if (refreshTimer !== null) window.clearInterval(refreshTimer)
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKeydown)
})

const dotClass: Record<ItemKind, string> = {
  red: 'bg-red-500',
  amber: 'bg-amber-500',
  sky: 'bg-sky-500',
}
</script>

<template>
  <div ref="root" class="relative">
    <Button
      variant="outline" size="icon" class="h-9 w-9"
      :aria-label="t('notify.bellAria', { count: unread.length })"
      :aria-expanded="open"
      @click="open = !open"
    >
      <Bell class="h-4 w-4 transition-transform" :class="unread.length > 0 && 'animate-swing'" />
      <span
        v-if="unread.length > 0"
        class="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white ring-2 ring-background"
      >{{ unread.length }}</span>
    </Button>

    <div
      v-if="open"
      class="absolute right-0 z-50 mt-1 w-[340px] overflow-hidden rounded-xl border border-border bg-popover text-popover-foreground shadow-lg sm:w-[380px]"
      role="dialog" :aria-label="t('notify.title')"
    >
      <!-- Шапка -->
      <div class="flex items-center justify-between gap-2 border-b border-zinc-100 px-4 py-3 dark:border-zinc-800">
        <div class="flex items-center gap-2">
          <p class="text-sm font-semibold">{{ t('notify.title') }}</p>
          <Badge v-if="unread.length > 0" class="bg-red-500 px-1.5 text-[10px] text-white">
            {{ t('notify.newCount', { count: unread.length }) }}
          </Badge>
          <Badge v-else variant="secondary" class="text-[10px]">{{ t('notify.allRead') }}</Badge>
        </div>
        <button
          :disabled="unread.length === 0"
          class="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] font-medium text-emerald-700 transition-colors hover:bg-emerald-50 disabled:cursor-default disabled:text-muted-foreground disabled:hover:bg-transparent dark:text-emerald-400 dark:hover:bg-emerald-950/40 dark:disabled:text-muted-foreground"
          @click="markAllRead"
        >
          <CheckCheck class="h-3.5 w-3.5" /> {{ t('notify.markAllRead') }}
        </button>
      </div>

      <!-- Фильтр -->
      <div class="flex gap-1 border-b border-zinc-100 px-3 py-2 dark:border-zinc-800">
        <button
          v-for="f in [{ id: 'all', label: t('notify.filterAll'), count: items.length },
                       { id: 'unread', label: t('notify.filterUnread'), count: unread.length }]"
          :key="f.id"
          :class="cn(
            'flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium transition-colors',
            filter === f.id
              ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300'
              : 'text-muted-foreground hover:bg-zinc-100 dark:hover:bg-zinc-800',
          )"
          @click="filter = f.id as 'all' | 'unread'"
        >
          {{ f.label }}
          <span
            :class="cn(
              'rounded-full px-1 text-[10px] font-bold',
              filter === f.id
                ? 'bg-emerald-600 text-white'
                : 'bg-zinc-200 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
            )"
          >{{ f.count }}</span>
        </button>
      </div>

      <!-- Список -->
      <div class="max-h-[320px] overflow-y-auto erp-scroll">
        <div
          v-if="shown.length === 0"
          class="flex flex-col items-center gap-2 px-4 py-10 text-center"
        >
          <span class="flex h-10 w-10 items-center justify-center rounded-full bg-zinc-100 dark:bg-zinc-800">
            <Inbox class="h-5 w-5 text-muted-foreground" />
          </span>
          <p class="text-sm font-medium">{{ t('notify.empty') }}</p>
          <p class="flex items-center gap-1 text-xs text-muted-foreground">
            <BellOff class="h-3 w-3" /> {{ t('notify.emptyHint') }}
          </p>
        </div>
        <button
          v-for="item in shown"
          :key="item.id"
          class="group flex w-full items-start gap-3 border-b border-zinc-100 px-4 py-3 text-left transition-colors last:border-0 hover:bg-zinc-50 dark:border-zinc-800/70 dark:hover:bg-zinc-800/50"
          :class="!isRead(item) && 'bg-emerald-50/50 dark:bg-emerald-950/20'"
          @click="openItem(item)"
        >
          <span
            class="mt-1.5 h-2 w-2 shrink-0 rounded-full"
            :class="cn(dotClass[item.kind], isRead(item) && 'opacity-30')"
          />
          <span class="min-w-0 flex-1">
            <span class="flex items-center justify-between gap-2">
              <span
                class="truncate text-[13px] leading-snug"
                :class="!isRead(item) ? 'font-semibold' : 'font-medium text-foreground/80'"
              >{{ item.title }}</span>
              <span v-if="item.time" class="shrink-0 whitespace-nowrap text-[10px] text-muted-foreground">{{ item.time }}</span>
            </span>
            <span class="mt-0.5 block truncate text-xs text-muted-foreground">{{ item.description }}</span>
            <span class="mt-1.5 inline-flex items-center gap-1 text-[11px] font-medium text-emerald-700 opacity-0 transition-opacity group-hover:opacity-100 dark:text-emerald-400">
              {{ t('notify.goTo') }} <ArrowRight class="h-3 w-3" />
            </span>
          </span>
        </button>
      </div>

      <!-- Футер -->
      <div class="border-t border-zinc-100 p-1.5 dark:border-zinc-800">
        <Button variant="ghost" size="sm" class="w-full justify-center gap-1.5 text-xs font-medium text-emerald-700 hover:text-emerald-800 dark:text-emerald-400 dark:hover:text-emerald-300">
          {{ t('notify.showAll') }}
        </Button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
// Полноэкранный список уведомлений (notifications-spec §8, этап A):
// серверный источник, PaginatedList (первые 50 + «Загрузить все»),
// фильтр all/unread, «Прочитать все», клик — прочитать + переход по link.
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { CheckCheck } from 'lucide-vue-next'
import {
  type NotificationItem, listNotificationsPage, markNotificationsRead,
} from '../api/notifications'
import { Button, EmptyState, Skeleton, ViewHeader, useToast } from '../components/ui'
import PaginatedList from '../components/ui/PaginatedList.vue'
import type { PageOf } from '../components/ui'
import { cn } from '../lib/utils'

const { t, d } = useI18n()
const router = useRouter()
const toast = useToast()

const filter = ref<'all' | 'unread'>('all')
const list = ref<{ reload: () => Promise<void> } | null>(null)

type ItemKind = 'red' | 'amber' | 'sky'

const severityKind: Record<string, ItemKind> = {
  critical: 'red',
  warning: 'amber',
  info: 'sky',
}

const dotClass: Record<ItemKind, string> = {
  red: 'bg-red-500',
  amber: 'bg-amber-500',
  sky: 'bg-sky-500',
}

function fetchPage(offset: number, limit: number): Promise<PageOf<NotificationItem>> {
  // PaginatedList при «Загрузить все» просит limit=0 — серверу нужен
  // конкретный размер, поэтому 0 трактуем как «большая страница»
  const size = limit > 0 ? limit : 100_000
  return listNotificationsPage({
    limit: size,
    offset,
    unread: filter.value === 'unread',
  })
}

async function markAllRead() {
  try {
    await markNotificationsRead({ all: true })
    await list.value?.reload()
  } catch (error) {
    toast.apiError(error)
  }
}

function openItem(item: NotificationItem) {
  if (!item.read_at) {
    item.read_at = new Date().toISOString()
    void markNotificationsRead({ ids: [item.id] }).catch(() => { /* выровняется при reload */ })
  }
  if (item.link) router.push(item.link)
}
</script>

<template>
  <div class="space-y-4">
    <ViewHeader :title="t('notify.listTitle')" :subtitle="t('notify.listSubtitle')">
      <template #actions>
        <Button variant="emerald" size="sm" class="gap-1.5" @click="markAllRead">
          <CheckCheck class="h-4 w-4" /> {{ t('notify.markAllRead') }}
        </Button>
      </template>
    </ViewHeader>

    <!-- Фильтр -->
    <div class="flex gap-1">
      <button
        v-for="f in [{ id: 'all', label: t('notify.filterAll') },
                     { id: 'unread', label: t('notify.filterUnread') }]"
        :key="f.id"
        :class="cn(
          'rounded-full px-3 py-1.5 text-xs font-medium transition-colors',
          filter === f.id
            ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300'
            : 'text-muted-foreground hover:bg-zinc-100 dark:hover:bg-zinc-800',
        )"
        @click="filter = f.id as 'all' | 'unread'"
      >
        {{ f.label }}
      </button>
    </div>

    <PaginatedList
      ref="list" :fetch-page="fetchPage" :reset-key="filter" v-slot="{ items: rows, loading }"
    >
      <div v-if="loading" class="space-y-2">
        <Skeleton class="h-14 w-full" />
        <Skeleton class="h-14 w-full" />
        <Skeleton class="h-14 w-full" />
      </div>
      <div v-else-if="rows.length === 0">
        <EmptyState :title="t('notify.empty')" :description="t('notify.emptyHint')" />
      </div>
      <div v-else class="overflow-hidden rounded-xl border border-border bg-card">
        <button
          v-for="row in rows"
          :key="row.id"
          class="group flex w-full items-start gap-3 border-b border-zinc-100 px-4 py-3 text-left transition-colors last:border-0 hover:bg-zinc-50 dark:border-zinc-800/70 dark:hover:bg-zinc-800/50"
          :class="!row.read_at && 'bg-emerald-50/50 dark:bg-emerald-950/20'"
          @click="openItem(row)"
        >
          <span
            class="mt-1.5 h-2 w-2 shrink-0 rounded-full"
            :class="cn(dotClass[severityKind[row.severity] ?? 'sky'], row.read_at && 'opacity-30')"
          />
          <span class="min-w-0 flex-1">
            <span class="flex items-center justify-between gap-2">
              <span
                class="truncate text-sm leading-snug"
                :class="!row.read_at ? 'font-semibold' : 'font-medium text-foreground/80'"
              >{{ row.title }}</span>
              <span class="shrink-0 whitespace-nowrap text-xs text-muted-foreground">
                {{ d(new Date(row.created_at), 'long') }}
              </span>
            </span>
            <span class="mt-0.5 block text-xs text-muted-foreground">{{ row.body }}</span>
          </span>
        </button>
      </div>
    </PaginatedList>
  </div>
</template>

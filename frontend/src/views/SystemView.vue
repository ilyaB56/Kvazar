<script setup lang="ts">
// Система (этап G — финальный дом в Настройках): версия и проверка
// обновлений, бэкапы с проверкой, параметр контура allow_negative_stock
// (полировка из реестра — настройка видна и меняется через /settings),
// журнал событий (events_log, админ).
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { DatabaseBackup, RefreshCw, ShieldCheck } from 'lucide-vue-next'
import { get, post, put } from '../api/client'
import type { Backup, SystemVersion } from '../api/types'
import {
  Badge, Button, Card, CardContent, EmptyState, Label, Skeleton, Switch, useToast,
} from '../components/ui'
import { useAuthStore } from '../stores/auth'

const { t, d } = useI18n()
const auth = useAuthStore()
const toast = useToast()

const backups = ref<Backup[] | null>(null)
const version = ref<SystemVersion | null>(null)
const loading = ref(true)
const creating = ref(false)
const verifying = ref<Record<string, boolean>>({})
const checking = ref(false)

// журнал (админ)
interface EventRow {
  id: number
  action: string
  entity_type: string
  created_at: string
}
const events = ref<EventRow[]>([])

// параметр контура: отсутствие строки = false (дефолт инвентаря)
const settingsMap = ref<Record<string, { value: string; value_type: string }>>({})
const negativeStock = ref(false)
const savingSetting = ref(false)

async function loadSettings() {
  try {
    const rows = await get<Array<{ key: string; value: string; value_type: string }>>('/settings')
    settingsMap.value = Object.fromEntries(rows.map((row) => [row.key, row]))
    negativeStock.value = settingsMap.value.allow_negative_stock?.value === 'true'
  } catch {
    settingsMap.value = {}
  }
}

async function toggleNegativeStock(value: boolean) {
  if (!auth.isAdmin) return
  savingSetting.value = true
  const prev = negativeStock.value
  negativeStock.value = value
  try {
    await put('/settings', { key: 'allow_negative_stock', value: String(value), value_type: 'bool' })
    toast.success(t('system.settingSaved'))
  } catch (error) {
    negativeStock.value = prev
    toast.apiError(error)
  } finally {
    savingSetting.value = false
  }
}

async function load() {
  loading.value = true
  try {
    const [backupList, versionInfo] = await Promise.all([
      get<Backup[]>('/system/backups'),
      get<SystemVersion>('/system/version'),
    ])
    backups.value = backupList
    version.value = versionInfo
    if (auth.isAdmin) {
      events.value = await get<EventRow[]>('/events/log?limit=20')
    }
    await loadSettings()
  } finally {
    loading.value = false
  }
}

// статусы создаются/проверяются в фоне — обновляем список, пока есть pending
function schedulePolling() {
  stopPolling()
  pollTimer.value = window.setInterval(async () => {
    backups.value = await get<Backup[]>('/system/backups')
    const busy = backups.value.some((b) => b.status === 'created' && b.kind === 'manual')
    if (!busy && !Object.values(verifying.value).some(Boolean)) stopPolling()
  }, 3000)
}

const pollTimer = ref<number | null>(null)
function stopPolling() {
  if (pollTimer.value !== null) {
    window.clearInterval(pollTimer.value)
    pollTimer.value = null
  }
}

async function createBackup() {
  creating.value = true
  try {
    await post('/system/backups')
    toast.success(t('system.backupQueued'))
    schedulePolling()
  } catch (error) {
    toast.apiError(error)
  } finally {
    creating.value = false
  }
}

async function verifyBackup(row: Backup) {
  verifying.value[row.id] = true
  try {
    await post(`/system/backups/${row.id}/verify`)
    toast.success(t('system.verifyQueued'))
    schedulePolling()
  } catch (error) {
    toast.apiError(error)
  } finally {
    verifying.value[row.id] = false
  }
}

async function checkNow() {
  checking.value = true
  try {
    await post('/system/update/check')
    version.value = await get<SystemVersion>('/system/version')
    toast.success(t('system.checked'))
  } catch (error) {
    toast.apiError(error)
  } finally {
    checking.value = false
  }
}

function formatSize(size: number): string {
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${size} B`
}

function backupTone(status: string): string {
  if (status === 'verified') return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300'
  if (status === 'failed') return 'bg-red-100 text-red-700 dark:bg-red-950/60 dark:text-red-300'
  return 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400'
}

onMounted(load)
onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="space-y-4">
    <!-- Версия и обновления -->
    <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <p class="text-sm font-semibold">{{ t('system.currentVersion') }}: {{ version?.version ?? '—' }}</p>
            <p v-if="version?.latest" class="text-xs text-amber-700 dark:text-amber-400">
              {{ t('system.availableVersion') }}: {{ version.latest.version }}
            </p>
            <p v-else class="text-xs text-muted-foreground">{{ t('system.noUpdate') }}</p>
          </div>
          <Button variant="outline" size="sm" class="gap-1.5" :disabled="checking" @click="checkNow">
            <RefreshCw :class="['h-3.5 w-3.5', checking && 'animate-spin']" /> {{ t('system.checkNow') }}
          </Button>
        </div>
        <p v-if="version?.latest?.changelog" class="mt-2 whitespace-pre-line text-xs text-muted-foreground">
          {{ version.latest.changelog }}
        </p>
        <p class="mt-2 text-xs text-muted-foreground">{{ t('system.updateHint') }}</p>
      </CardContent>
    </Card>

    <!-- Параметры контура -->
    <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="space-y-4 p-5">
        <p class="flex items-center gap-2 text-sm font-semibold">
          <DatabaseBackup class="h-4 w-4 text-emerald-600" /> {{ t('system.paramsTitle') }}
        </p>
        <div class="flex items-start justify-between gap-4">
          <div>
            <Label class="text-sm">{{ t('system.negativeStock') }}</Label>
            <p class="mt-0.5 max-w-lg text-xs leading-relaxed text-muted-foreground">
              {{ t('system.negativeStockHint') }}
            </p>
          </div>
          <Switch
            :model-value="negativeStock"
            :disabled="!auth.isAdmin || savingSetting"
            @update:model-value="toggleNegativeStock($event)"
          />
        </div>
      </CardContent>
    </Card>

    <!-- Бэкапы -->
    <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-0">
        <div class="flex items-center justify-between px-4 py-3">
          <p class="text-sm font-semibold">{{ t('system.backups') }}</p>
          <Button variant="emerald" size="sm" class="gap-1.5" :disabled="creating" @click="createBackup">
            <DatabaseBackup class="h-3.5 w-3.5" /> {{ t('system.createBackup') }}
          </Button>
        </div>
        <div v-if="loading" class="space-y-2 p-4"><Skeleton class="h-10 w-full" /></div>
        <div v-else-if="backups?.length === 0" class="p-6">
          <EmptyState :title="t('ui.emptyTitle')" :description="t('system.noBackups')" />
        </div>
        <div v-else class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                <th class="px-3 py-2 font-medium">{{ t('system.backupDate') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('system.backupSize') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('system.backupKind') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('system.backupStatus') }}</th>
                <th class="hidden px-3 py-2 font-medium lg:table-cell">{{ t('system.backupFile') }}</th>
                <th class="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in backups ?? []" :key="row.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
                <td class="whitespace-nowrap px-3 py-2">{{ d(row.created_at, 'short') }}</td>
                <td class="px-3 py-2 text-muted-foreground">{{ formatSize(row.size) }}</td>
                <td class="px-3 py-2">{{ t(`system.kind.${row.kind}`) }}</td>
                <td class="px-3 py-2">
                  <Badge :class="backupTone(row.status)">{{ t(`system.status.${row.status}`) }}</Badge>
                </td>
                <td class="hidden max-w-[240px] truncate px-3 py-2 font-mono text-xs text-muted-foreground lg:table-cell">
                  {{ row.file_name }}
                </td>
                <td class="px-3 py-2 text-right">
                  <Button
                    variant="outline" size="sm" class="gap-1.5" :disabled="verifying[row.id]"
                    @click="verifyBackup(row)"
                  >
                    <ShieldCheck class="h-3.5 w-3.5" /> {{ t('system.verify') }}
                  </Button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>

    <!-- Журнал событий (админ) -->
    <Card v-if="auth.isAdmin" class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-0">
        <div class="flex items-center justify-between px-4 py-3">
          <p class="text-sm font-semibold">{{ t('system.eventLog') }}</p>
          <Button variant="ghost" size="sm" class="gap-1.5" @click="load">
            <RefreshCw class="h-3.5 w-3.5" /> {{ t('sync.refresh') }}
          </Button>
        </div>
        <div class="max-h-[320px] overflow-y-auto">
          <table class="w-full text-sm">
            <tbody>
              <tr v-for="event in events" :key="event.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
                <td class="whitespace-nowrap px-3 py-1.5 font-mono text-xs text-emerald-700 dark:text-emerald-400">
                  {{ event.action }}
                </td>
                <td class="px-3 py-1.5 text-xs text-muted-foreground">{{ event.entity_type }}</td>
                <td class="whitespace-nowrap px-3 py-1.5 text-right text-xs text-muted-foreground">
                  {{ d(event.created_at, 'short') }}
                </td>
              </tr>
              <tr v-if="events.length === 0">
                <td class="px-3 py-6 text-center text-sm text-muted-foreground">—</td>
              </tr>
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  </div>
</template>

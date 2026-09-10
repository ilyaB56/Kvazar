<script setup lang="ts">
// Синхронизации (этап G — переприход на ui-библиотеку): задания, запуск
// вручную, журнал прогонов выбранного задания с автоповтором после запуска.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Play, RefreshCw } from 'lucide-vue-next'
import { get, post } from '../api/client'
import type { SyncJob, SyncRun } from '../api/types'
import { Badge, Button, Card, CardContent, EmptyState, Skeleton, useToast } from '../components/ui'
import { useAuthStore } from '../stores/auth'

const { t, d } = useI18n()
const auth = useAuthStore()
const toast = useToast()
const canWrite = computed(() => auth.moduleLevel('integrations') === 'rw')

const loading = ref(true)
const jobs = ref<SyncJob[]>([])
const jobSearch = ref('')
const filteredJobs = computed(() => {
  const q = jobSearch.value.trim().toLowerCase()
  return q ? jobs.value.filter((j) => j.name.toLowerCase().includes(q) || (j.endpoint || '').toLowerCase().includes(q)) : jobs.value
})
const selected = ref<SyncJob | null>(null)
const runs = ref<SyncRun[]>([])
const runsLoading = ref(false)
const running = ref<Record<string, boolean>>({})

// автоповтор журнала: раз в 5 с в течение 30 с после «Запустить сейчас»
let autoTimer: number | null = null
let autoAttempts = 0

async function loadJobs() {
  jobs.value = await get<SyncJob[]>('/integrations/sync-jobs')
  if (!selected.value && jobs.value.length) {
    selectJob(jobs.value[0])
  }
}

async function loadRuns() {
  if (!selected.value) return
  runsLoading.value = true
  try {
    runs.value = await get<SyncRun[]>(`/integrations/sync-runs?sync_job_id=${selected.value.id}`)
  } finally {
    runsLoading.value = false
  }
}

function selectJob(job: SyncJob) {
  selected.value = job
  runs.value = []
  void loadRuns()
}

function scheduleRunsAutoRefresh() {
  stopAutoRefresh()
  autoAttempts = 0
  autoTimer = window.setInterval(() => {
    autoAttempts += 1
    void loadRuns()
    if (autoAttempts >= 6) stopAutoRefresh()
  }, 5000)
}

function stopAutoRefresh() {
  if (autoTimer !== null) {
    window.clearInterval(autoTimer)
    autoTimer = null
  }
}

async function runNow(job: SyncJob) {
  running.value[job.id] = true
  try {
    await post(`/integrations/sync-jobs/${job.id}/run`)
    toast.success(t('sync.queued'))
    if (selected.value?.id === job.id) scheduleRunsAutoRefresh()
  } catch (error) {
    toast.apiError(error)
  } finally {
    running.value[job.id] = false
  }
}

onMounted(async () => {
  try {
    await loadJobs()
  } finally {
    loading.value = false
  }
})
onBeforeUnmount(stopAutoRefresh)
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between gap-3">
        <p class="text-base font-semibold">{{ t('sync.title') }}</p>
        <Input v-model="jobSearch" :placeholder="t('ui.searchPlaceholder')" class="h-8 w-[220px]" />
      </div>

    <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-0">
        <div v-if="loading" class="space-y-2 p-4">
          <Skeleton class="h-10 w-full" />
          <Skeleton class="h-10 w-full" />
        </div>
        <div v-else-if="jobs.length === 0" class="p-6">
          <EmptyState :title="t('ui.emptyTitle')" :description="t('sync.empty')" />
        </div>
        <div v-else class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                <th class="px-3 py-2 font-medium">{{ t('sync.name') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('sync.direction') }}</th>
                <th class="hidden px-3 py-2 font-medium sm:table-cell">{{ t('sync.cron') }}</th>
                <th class="hidden px-3 py-2 font-medium md:table-cell">{{ t('sync.endpoint') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('sync.active') }}</th>
                <th v-if="canWrite" class="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="job in jobs" :key="job.id"
                class="cursor-pointer border-t border-zinc-100 transition-colors hover:bg-zinc-50/60 dark:border-zinc-800/70 dark:hover:bg-zinc-800/40"
                :class="selected?.id === job.id && 'bg-emerald-50/50 dark:bg-emerald-950/20'"
                @click="selectJob(job)"
              >
                <td class="px-3 py-2 font-medium">{{ job.name }}</td>
                <td class="px-3 py-2">
                  <Badge variant="outline">
                    {{ job.direction === 'fetch' ? t('sync.directionFetch') : t('sync.directionPush') }}
                  </Badge>
                </td>
                <td class="hidden px-3 py-2 font-mono text-xs text-muted-foreground sm:table-cell">{{ job.cron }}</td>
                <td class="hidden px-3 py-2 text-muted-foreground md:table-cell">{{ job.endpoint || '—' }}</td>
                <td class="px-3 py-2 text-muted-foreground">{{ job.is_active ? t('sync.yes') : t('sync.no') }}</td>
                <td v-if="canWrite" class="px-3 py-2 text-right" @click.stop>
                  <Button
                    variant="outline" size="sm" class="gap-1.5" :disabled="running[job.id]"
                    @click="runNow(job)"
                  >
                    <Play class="h-3.5 w-3.5" /> {{ t('sync.runNow') }}
                  </Button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>

    <Card v-if="selected" class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-0">
        <div class="flex items-center justify-between px-4 py-3">
          <p class="text-sm font-semibold">
            {{ t('sync.runs') }} — {{ selected.name }}
            <span class="ml-1 text-xs font-normal text-muted-foreground">{{ selected.endpoint }}</span>
          </p>
          <Button variant="ghost" size="icon" class="h-7 w-7" :title="t('sync.refresh')" @click="loadRuns">
            <RefreshCw :class="['h-3.5 w-3.5', runsLoading && 'animate-spin']" />
          </Button>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                <th class="px-3 py-2 font-medium">{{ t('sync.status') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('sync.itemsIn') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('sync.itemsOut') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('sync.error') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('sync.time') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="run in runs" :key="run.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
                <td class="px-3 py-2">
                  <Badge :class="run.status === 'success'
                    ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300'
                    : 'bg-red-100 text-red-700 dark:bg-red-950/60 dark:text-red-300'">
                    {{ run.status === 'success' ? t('sync.statusSuccess') : t('sync.statusError') }}
                  </Badge>
                </td>
                <td class="px-3 py-2">{{ run.items_in }}</td>
                <td class="px-3 py-2">{{ run.items_out }}</td>
                <td class="max-w-[280px] px-3 py-2 text-xs text-red-600 dark:text-red-400">
                  <span class="break-all">{{ run.error || '—' }}</span>
                </td>
                <td class="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">{{ d(run.started_at, 'short') }}</td>
              </tr>
              <tr v-if="runs.length === 0">
                <td colspan="5" class="px-3 py-6 text-center text-sm text-muted-foreground">{{ t('sync.runsEmpty') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  </div>
</template>

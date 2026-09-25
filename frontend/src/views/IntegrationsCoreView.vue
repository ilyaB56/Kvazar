<script setup lang="ts">
// Экран интеграционного ядра (этап G, эталон integrations-view.tsx §2 v0.11):
// KPI ядра, реестр коннекторов карточками со статусами, панель изоляции
// контура, live-журнал из реальных sync_runs + events_log. Макетные
// «блокировки прямого доступа» не переносим: изоляция — правило разработки
// (ADR-001, сеть только в connectors), рантайм-счётчика нет; в журнале
// только факты.
// Реестр коннекторов пагинирован (PaginatedList, по 50); полный список
// соединений грузится только для KPI (счётчик «ok из всех»). Прогоны
// live-журнала берутся с limit=10 на задание (свежие, сервер сортирует
// по id desc) — раньше тянулась вся история прогонов каждого задания.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import {
  Activity, ArrowDownToLine, ArrowUpFromLine, Clock3, Cpu, Play, PlugZap, ShieldCheck,
} from 'lucide-vue-next'
import { get, post, put } from '../api/client'
import {
  Badge, Button, Card, CardContent, Dialog, EmptyState, Input, PaginatedList,
  Skeleton, Switch, useToast,
} from '../components/ui'
import type { PageOf } from '../components/ui'
import { useAuthStore } from '../stores/auth'

const { t, d } = useI18n()
const router = useRouter()
const auth = useAuthStore()
const toast = useToast()

interface Connection {
  id: string
  name: string
  connector_code: string
  is_active: boolean
  last_check_ok: boolean | null
  last_check_at: string | null
}
interface SyncJob { id: string; name: string; is_active: boolean }
interface SyncRun {
  id: number
  sync_job_id: string
  status: string
  items_in: number
  items_out: number
  started_at: string
  jobName?: string
}
interface EventRow { id: number; action: string; entity_type: string; created_at: string }

const loading = ref(true)
const jobs = ref<SyncJob[]>([])
const runs = ref<SyncRun[]>([])
const events = ref<EventRow[]>([])
const queueSize = ref<number | null>(null)
const paused = ref(false)

// total приходит из конверта PaginatedList (@loaded) — полный список
// ради KPI больше не грузим
const connectionsTotal = ref(0)
function onConnectionsLoaded(total: number) {
  connectionsTotal.value = total
}
const runs24h = computed(() => {
  const dayAgo = Date.now() - 24 * 3600 * 1000
  return runs.value.filter((run) => Date.parse(run.started_at) >= dayAgo).length
})
const activeJobs = computed(() => jobs.value.filter((job) => job.is_active).length)

const testing = ref<Record<string, boolean>>({})

// ----- Реестр коннекторов (пагинировано) -----
const connectionsList = ref<{ reload: () => Promise<void> } | null>(null)

function fetchConnectionsPage(offset: number, limit: number) {
  return get<PageOf<Connection>>(`/integrations/connections?limit=${limit}&offset=${offset}`)
}

// ----- KPI + live-журнал -----
async function loadAll() {
  try {
    const jobList = await get<SyncJob[]>('/integrations/sync-jobs')
    jobs.value = jobList
    // live-журнал: свежие прогоны активных заданий (limit=10 на задание)
    const active = jobList.filter((job) => job.is_active)
    const runLists = await Promise.all(
      active.map((job) =>
        get<PageOf<SyncRun>>(`/integrations/sync-runs?sync_job_id=${job.id}&limit=10`)
          .then((page) => page.items.map((row) => ({ ...row, jobName: job.name })))
          .catch(() => [] as SyncRun[]),
      ),
    )
    runs.value = runLists.flat().sort((a, b) => b.started_at.localeCompare(a.started_at)).slice(0, 30)
    if (auth.isAdmin) {
      try {
        const outbox = await get<Array<{ processed: boolean }>>('/events/outbox?limit=500')
        queueSize.value = outbox.filter((row) => !row.processed).length
        events.value = await get<EventRow[]>('/events/log?limit=10')
      } catch {
        queueSize.value = null
      }
    }
  } catch {
    toast.error(t('errors.unknown'))
  } finally {
    loading.value = false
  }
}
onMounted(loadAll)

// ----- Безопасность сети: egress allowlist (P1 п.7, платформенный) -----
interface EgressSettings { allowlist: string[]; strict: boolean }
const egress = ref<EgressSettings | null>(null)
const egressDomain = ref('')
const egressStrictLocal = ref(false)
const egressSaving = ref(false)

async function loadEgress() {
  if (!auth.user?.is_platform_admin) return
  try {
    egress.value = await get<EgressSettings>('/platform/egress-settings')
    egressStrictLocal.value = egress.value.strict
  } catch { /* не платформенный или недоступно */ }
}
onMounted(loadEgress)

function addDomain() {
  const domain = egressDomain.value.trim().toLowerCase()
  if (!domain || !egress.value) return
  if (!/^[a-z0-9.-]{2,253}$/.test(domain)) {
    toast.error(t('integrations.egressInvalidDomain'))
    return
  }
  if (egress.value.allowlist.includes(domain)) return
  if (!window.confirm(t('integrations.egressAddConfirm', { domain }))) return
  egress.value = { ...egress.value, allowlist: [...egress.value.allowlist, domain].sort() }
  void saveEgress()
  egressDomain.value = ''
}

function removeDomain(domain: string) {
  if (!egress.value) return
  if (!window.confirm(t('integrations.egressRemoveConfirm', { domain }))) return
  egress.value = { ...egress.value, allowlist: egress.value.allowlist.filter((d) => d !== domain) }
  void saveEgress()
}

function toggleStrict(value: boolean) {
  if (!egress.value) return
  if (value && !window.confirm(t('integrations.egressStrictWarning'))) return
  egressStrictLocal.value = value
  egress.value = { ...egress.value, strict: value }
  void saveEgress()
}

async function saveEgress() {
  if (!egress.value || egressSaving.value) return
  egressSaving.value = true
  try {
    egress.value = await put<EgressSettings>('/platform/egress-settings', {
      allowlist: egress.value.allowlist, strict: egress.value.strict,
    })
    egressStrictLocal.value = egress.value.strict
    toast.success(t('integrations.egressSaved'))
  } catch (error) {
    toast.apiError(error)
    await loadEgress()
  } finally {
    egressSaving.value = false
  }
}

let refreshTimer: number | undefined
onMounted(() => {
  refreshTimer = window.setInterval(() => { if (!paused.value) void loadAll() }, 10_000)
})
onBeforeUnmount(() => window.clearInterval(refreshTimer))

async function testConnection(connection: Connection) {
  testing.value[connection.id] = true
  try {
    const result = await post<{ ok: boolean; error: string | null }>(
      `/integrations/connections/${connection.id}/test`)
    if (result.ok) toast.success(t('connections.testOk'))
    else toast.error(`${t('connections.testFailed')}: ${result.error ?? ''}`)
    await connectionsList.value?.reload()
    await loadAll()
  } catch (error) {
    toast.apiError(error)
  } finally {
    testing.value[connection.id] = false
  }
}

function statusMeta(connection: Connection): { label: string; badge: string; icon: string } {
  if (connection.last_check_ok === true) {
    return {
      label: t('integrations.statusConnected'),
      badge: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300',
      icon: 'bg-emerald-50 text-emerald-600 dark:bg-emerald-950/60 dark:text-emerald-400',
    }
  }
  if (connection.last_check_ok === false) {
    return {
      label: t('integrations.statusError'),
      badge: 'bg-red-100 text-red-700 dark:bg-red-950/60 dark:text-red-300',
      icon: 'bg-red-50 text-red-600 dark:bg-red-950/60 dark:text-red-400',
    }
  }
  return {
    label: t('integrations.statusUnknown'),
    badge: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
    icon: 'bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400',
  }
}

const kpis = computed(() => [
  {
    icon: PlugZap, value: String(connectionsTotal.value),
    label: t('integrations.kpiConnectors'), sub: t('integrations.kpiConnectorsSub'),
  },
  {
    icon: Activity, value: String(runs24h.value),
    label: t('integrations.kpiEvents24h'), sub: t('integrations.kpiEvents24hSub'),
  },
  {
    icon: Clock3, value: queueSize.value === null ? '—' : String(queueSize.value),
    label: t('integrations.kpiQueue'), sub: t('integrations.kpiQueueSub'),
  },
  {
    icon: Cpu, value: String(activeJobs.value),
    label: t('integrations.kpiJobs'), sub: t('integrations.kpiJobsSub'),
  },
])
</script>

<template>
  <div class="space-y-4">
    <!-- KPI ядра -->
    <div v-if="loading" class="grid grid-cols-2 gap-4 xl:grid-cols-4">
      <Skeleton v-for="i in 4" :key="i" class="h-24 w-full" />
    </div>
    <div v-else class="grid grid-cols-2 gap-4 xl:grid-cols-4">
      <div
        v-for="kpi in kpis" :key="kpi.label"
        class="rounded-xl border border-zinc-200 bg-card p-4 transition-colors hover:border-emerald-300 dark:border-zinc-800 dark:hover:border-emerald-800"
      >
        <div class="flex items-center justify-between gap-2">
          <p class="text-xs text-muted-foreground">{{ kpi.label }}</p>
          <component :is="kpi.icon" class="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
        </div>
        <p class="mt-1.5 text-2xl font-bold tracking-tight">{{ kpi.value }}</p>
        <p class="mt-0.5 text-[11px] text-muted-foreground">{{ kpi.sub }}</p>
      </div>
    </div>

    <!-- Безопасность сети: egress allowlist (P1 п.7, платформенный админ) -->
    <Card v-if="egress" class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="flex items-center gap-2 text-sm font-semibold">
            <ShieldCheck class="h-4 w-4 text-emerald-600" />
            {{ t('integrations.egressTitle') }}
          </p>
          <label class="flex items-center gap-2 text-xs text-muted-foreground">
            {{ t('integrations.egressStrict') }}
            <Switch
              :model-value="egressStrictLocal"
              :disabled="egressSaving"
              @update:model-value="toggleStrict"
            />
          </label>
        </div>
        <p class="mt-1 text-xs text-muted-foreground">{{ t('integrations.egressHint') }}</p>

        <div class="mt-3 flex flex-wrap items-center gap-2">
          <Input
            v-model="egressDomain" :placeholder="t('integrations.egressDomainPlaceholder')"
            class="h-9 max-w-xs" :disabled="egressSaving"
            @keydown.enter="addDomain"
          />
          <Button variant="outline" size="sm" :disabled="egressSaving" @click="addDomain">
            {{ t('integrations.egressAddDomain') }}
          </Button>
        </div>

        <div class="mt-3 flex flex-wrap gap-1.5">
          <span
            v-for="domain in egress.allowlist" :key="domain"
            class="group inline-flex items-center gap-1 rounded-full bg-zinc-100 px-2.5 py-1 font-mono text-xs dark:bg-zinc-800"
          >
            {{ domain }}
            <button
              type="button" class="text-zinc-400 transition-colors hover:text-red-500"
              :title="t('integrations.egressRemove')" :disabled="egressSaving"
              @click="removeDomain(domain)"
            >×</button>
          </span>
          <span v-if="!egress.allowlist.length" class="text-xs text-muted-foreground">—</span>
        </div>
      </CardContent>
    </Card>

    <div class="grid grid-cols-1 gap-4 xl:grid-cols-3">
      <!-- Реестр коннекторов -->
      <Card class="border-zinc-200 shadow-sm dark:border-zinc-800 xl:col-span-2">
        <CardContent class="p-5">
          <PaginatedList
            ref="connectionsList" :fetch-page="fetchConnectionsPage"
            @loaded="onConnectionsLoaded"
            v-slot="{ items: connectionRows, loading }"
          >
            <div class="flex items-center gap-2">
              <span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-violet-500/10 text-violet-600 dark:text-violet-300">
                <PlugZap class="h-4 w-4" />
              </span>
              <div>
                <p class="text-base font-semibold">{{ t('integrations.registry') }}</p>
                <p class="text-xs text-muted-foreground">{{ t('integrations.registrySub') }}</p>
              </div>
              <Button variant="ghost" size="sm" class="ml-auto text-xs" @click="router.push('/integrations/connections')">
                {{ t('integrations.manage') }}
              </Button>
            </div>
            <div v-if="loading" class="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Skeleton v-for="i in 4" :key="i" class="h-36 w-full" />
            </div>
            <div v-else-if="connectionRows.length === 0" class="mt-4">
              <EmptyState :title="t('ui.emptyTitle')" :description="t('connections.empty')" />
            </div>
            <div v-else class="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div
                v-for="connection in connectionRows" :key="connection.id"
                class="group flex flex-col rounded-xl border bg-card p-4 transition-all hover:-translate-y-0.5 hover:shadow-md"
                :class="connection.last_check_ok === false
                  ? 'border-red-300 dark:border-red-900/60'
                  : 'border-zinc-200 hover:border-emerald-300 dark:border-zinc-800 dark:hover:border-emerald-800'"
              >
                <div class="flex items-start justify-between gap-2">
                  <span :class="['flex h-9 w-9 shrink-0 items-center justify-center rounded-lg', statusMeta(connection).icon]">
                    <PlugZap class="h-4 w-4" />
                  </span>
                  <Badge :class="statusMeta(connection).badge">{{ statusMeta(connection).label }}</Badge>
                </div>
                <p class="mt-2.5 text-sm font-semibold leading-tight">{{ connection.name }}</p>
                <p class="mt-0.5 text-[11px] text-muted-foreground">{{ connection.connector_code }}</p>
                <div class="mt-3 flex items-center justify-between gap-2 border-t border-zinc-100 pt-2.5 dark:border-zinc-800">
                  <span class="flex min-w-0 items-center gap-1 text-[11px] text-muted-foreground">
                    <Clock3 class="h-3 w-3 shrink-0" />
                    <span class="truncate">
                      {{ connection.last_check_at ? d(connection.last_check_at, 'short') : t('integrations.neverChecked') }}
                    </span>
                  </span>
                  <Button
                    variant="outline" size="sm"
                    class="h-7 shrink-0 gap-1 px-2 text-[11px]"
                    :disabled="testing[connection.id]"
                    @click="testConnection(connection)"
                  >
                    <Play class="h-3 w-3" /> {{ testing[connection.id] ? t('connections.testing') : t('integrations.recheck') }}
                  </Button>
                </div>
              </div>
            </div>
          </PaginatedList>
        </CardContent>
      </Card>

      <div class="space-y-4">
        <!-- Изоляция контура -->
        <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
          <CardContent class="p-5">
            <p class="flex items-center gap-2 text-sm font-semibold">
              <ShieldCheck class="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
              {{ t('integrations.isolationTitle') }}
            </p>
            <p class="mt-2 text-xs leading-relaxed text-muted-foreground">
              {{ t('integrations.isolationText') }}
            </p>
            <ul class="mt-3 space-y-1.5 text-xs">
              <li class="flex items-center gap-2">
                <span class="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                {{ t('integrations.isolationRule1') }}
              </li>
              <li class="flex items-center gap-2">
                <span class="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                {{ t('integrations.isolationRule2') }}
              </li>
              <li class="flex items-center gap-2">
                <span class="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                {{ t('integrations.isolationRule3') }}
              </li>
            </ul>
          </CardContent>
        </Card>

        <!-- Live-журнал -->
        <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
          <CardContent class="p-5">
            <div class="flex items-center justify-between">
              <p class="text-sm font-semibold">{{ t('integrations.liveLog') }}</p>
              <Button variant="ghost" size="sm" class="text-xs" @click="paused = !paused">
                {{ paused ? t('integrations.resume') : t('integrations.pause') }}
              </Button>
            </div>
            <div class="mt-3 max-h-[340px] space-y-1.5 overflow-y-auto font-mono text-[11px] leading-relaxed">
              <p
                v-for="run in runs.slice(0, 15)" :key="run.id"
                class="flex items-start gap-1.5"
              >
                <component
                  :is="run.status === 'error' ? ShieldCheck : run.items_out > 0 ? ArrowUpFromLine : ArrowDownToLine"
                  :class="['mt-0.5 h-3 w-3 shrink-0',
                    run.status === 'error' ? 'text-red-500' : run.items_out > 0 ? 'text-emerald-500' : 'text-sky-500']"
                />
                <span class="min-w-0 flex-1 break-all">
                  {{ run.jobName ?? run.sync_job_id.slice(0, 8) }}
                  · {{ run.status }} · {{ run.items_in }}/{{ run.items_out }}
                  <span class="text-muted-foreground">{{ d(run.started_at, 'short') }}</span>
                </span>
              </p>
              <p v-if="runs.length === 0" class="text-muted-foreground">{{ t('integrations.noRuns') }}</p>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  </div>
</template>

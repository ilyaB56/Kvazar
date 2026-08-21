<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { CaretRight, Refresh } from '@element-plus/icons-vue'
import { get, post } from '../api/client'
import type { SyncJob, SyncRun } from '../api/types'

const { t } = useI18n()

const jobs = ref<SyncJob[]>([])
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
  loadRuns()
}

function scheduleRunsAutoRefresh() {
  stopAutoRefresh()
  autoAttempts = 0
  autoTimer = window.setInterval(() => {
    autoAttempts += 1
    loadRuns()
    if (autoAttempts >= 6) {
      stopAutoRefresh()
    }
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
    ElMessage.success(t('sync.queued'))
    if (selected.value?.id === job.id) {
      scheduleRunsAutoRefresh()
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('errors.unknown'))
  } finally {
    running.value[job.id] = false
  }
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString('ru-RU')
}

onMounted(loadJobs)
onBeforeUnmount(stopAutoRefresh)
</script>

<template>
  <div class="page">
    <div class="page__header">
      <h2 class="page__title">{{ t('sync.title') }}</h2>
    </div>

    <el-table
      :data="jobs"
      highlight-current-row
      @current-change="(job: SyncJob | null) => job && selectJob(job)"
    >
      <el-table-column prop="name" :label="t('sync.name')" min-width="160" />
      <el-table-column :label="t('sync.direction')" width="130">
        <template #default="{ row }">
          <el-tag>{{ row.direction === 'fetch' ? t('sync.directionFetch') : t('sync.directionPush') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="cron" :label="t('sync.cron')" width="120" />
      <el-table-column prop="endpoint" :label="t('sync.endpoint')" min-width="140" />
      <el-table-column :label="t('sync.active')" width="90">
        <template #default="{ row }">
          {{ row.is_active ? t('sync.yes') : t('sync.no') }}
        </template>
      </el-table-column>
      <el-table-column width="200">
        <template #default="{ row }">
          <el-button
            size="small"
            type="primary"
            :icon="CaretRight"
            :loading="running[row.id]"
            @click.stop="runNow(row)"
          >
            {{ t('sync.runNow') }}
          </el-button>
        </template>
      </el-table-column>
      <template #empty>
        {{ t('sync.empty') }}
      </template>
    </el-table>

    <div v-if="selected" class="runs">
      <div class="page__header">
        <h3 class="page__title">
          {{ t('sync.runs') }} — {{ selected.name }}
          <span class="muted">{{ selected.endpoint }}</span>
        </h3>
        <el-button :icon="Refresh" :loading="runsLoading" @click="loadRuns">
          {{ t('sync.refresh') }}
        </el-button>
      </div>
      <el-table :data="runs" v-loading="runsLoading" size="small">
        <el-table-column :label="t('sync.status')" width="110">
          <template #default="{ row }">
            <el-tag :type="row.status === 'success' ? 'success' : 'danger'">
              {{ row.status === 'success' ? t('sync.statusSuccess') : t('sync.statusError') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="items_in" :label="t('sync.itemsIn')" width="110" />
        <el-table-column prop="items_out" :label="t('sync.itemsOut')" width="110" />
        <el-table-column :label="t('sync.error')" min-width="200">
          <template #default="{ row }">
            <span class="runs__error">{{ row.error }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('sync.time')" width="170">
          <template #default="{ row }">
            {{ formatTime(row.started_at) }}
          </template>
        </el-table-column>
        <template #empty>
          {{ t('sync.runsEmpty') }}
        </template>
      </el-table>
    </div>
    <el-empty v-else :description="t('sync.noSelection')" />
  </div>
</template>

<style scoped>
.runs {
  margin-top: 24px;
}

.runs__error {
  font-size: 12px;
  color: #f56c6c;
  word-break: break-all;
}
</style>

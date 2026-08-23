<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Refresh, RefreshRight } from '@element-plus/icons-vue'
import { get, post } from '../api/client'
import type { Backup, SystemVersion } from '../api/types'

const { t } = useI18n()

const backups = ref<Backup[]>([])
const version = ref<SystemVersion | null>(null)
const loading = ref(false)
const creating = ref(false)
const verifying = ref<Record<string, boolean>>({})
const checking = ref(false)

let pollTimer: number | null = null

async function load() {
  loading.value = true
  try {
    const [backupList, versionInfo] = await Promise.all([
      get<Backup[]>('/system/backups'),
      get<SystemVersion>('/system/version'),
    ])
    backups.value = backupList
    version.value = versionInfo
  } finally {
    loading.value = false
  }
}

// статусы создаются/проверяются в фоне — обновляем список, пока есть pending-операции
function schedulePolling() {
  stopPolling()
  pollTimer = window.setInterval(async () => {
    backups.value = await get<Backup[]>('/system/backups')
    const busy = backups.value.some((b) => b.status === 'created' && b.kind === 'manual')
    if (!busy && !Object.values(verifying.value).some(Boolean)) stopPolling()
  }, 3000)
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

async function createBackup() {
  creating.value = true
  try {
    await post('/system/backups')
    ElMessage.success(t('system.backupQueued'))
    schedulePolling()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('errors.unknown'))
  } finally {
    creating.value = false
  }
}

async function verifyBackup(row: Backup) {
  verifying.value[row.id] = true
  try {
    await post(`/system/backups/${row.id}/verify`)
    ElMessage.success(t('system.verifyQueued'))
    schedulePolling()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('errors.unknown'))
  } finally {
    verifying.value[row.id] = false
  }
}

async function checkNow() {
  checking.value = true
  try {
    await post('/system/update/check')
    version.value = await get<SystemVersion>('/system/version')
    ElMessage.success(t('system.checked'))
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('errors.unknown'))
  } finally {
    checking.value = false
  }
}

function formatSize(size: number): string {
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${size} B`
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString('ru-RU')
}

onMounted(load)
onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="page">
    <div class="page__header">
      <h2 class="page__title">{{ t('system.title') }}</h2>
      <el-button :icon="Refresh" :loading="loading" @click="load">
        {{ t('sync.refresh') }}
      </el-button>
    </div>

    <el-card v-if="version" class="version-card">
      <div class="version-card__row">
        <span>{{ t('system.currentVersion') }}: <b>{{ version.version }}</b></span>
        <span v-if="version.latest" class="version-card__latest">
          {{ t('system.availableVersion') }}:
          <b>{{ version.latest.version }}</b>
        </span>
        <span v-else class="muted">{{ t('system.noUpdate') }}</span>
        <el-button size="small" :loading="checking" @click="checkNow">
          {{ t('system.checkNow') }}
        </el-button>
      </div>
      <div v-if="version.latest?.changelog" class="version-card__changelog">
        {{ version.latest.changelog }}
      </div>
      <div class="muted version-card__hint">{{ t('system.updateHint') }}</div>
    </el-card>

    <div class="page__header">
      <h3 class="page__title">{{ t('system.backups') }}</h3>
      <el-button type="primary" :loading="creating" @click="createBackup">
        {{ t('system.createBackup') }}
      </el-button>
    </div>
    <el-table :data="backups" v-loading="loading">
      <el-table-column :label="t('system.backupDate')" width="180">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column :label="t('system.backupSize')" width="110">
        <template #default="{ row }">{{ formatSize(row.size) }}</template>
      </el-table-column>
      <el-table-column :label="t('system.backupKind')" width="130">
        <template #default="{ row }">{{ t(`system.kind.${row.kind}`) }}</template>
      </el-table-column>
      <el-table-column :label="t('system.backupStatus')" width="130">
        <template #default="{ row }">
          <el-tag :type="row.status === 'verified' ? 'success' : row.status === 'failed' ? 'danger' : 'info'">
            {{ t(`system.status.${row.status}`) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="file_name" :label="t('system.backupFile')" min-width="240" />
      <el-table-column width="220">
        <template #default="{ row }">
          <el-button size="small" :icon="RefreshRight" :loading="verifying[row.id]" @click="verifyBackup(row)">
            {{ t('system.verify') }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
.version-card {
  margin-bottom: 20px;
}

.version-card__row {
  display: flex;
  gap: 24px;
  align-items: baseline;
}

.version-card__latest {
  color: #e6a23c;
}

.version-card__changelog {
  margin-top: 8px;
  white-space: pre-line;
  color: #606266;
  font-size: 13px;
}

.version-card__hint {
  margin-top: 8px;
}

.version-card__row {
  display: flex;
  gap: 24px;
  align-items: center;
}
</style>

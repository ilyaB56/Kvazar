<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Bell, Delete, Position } from '@element-plus/icons-vue'
import { get, post, del } from '../api/client'

const { t } = useI18n()

interface NotificationRule {
  id: string
  name: string
  event_name: string
  chat_id: string
  template: string
  is_active: boolean
}

const EVENTS = [
  'acc.transaction.posted',
  'acc.period.closed',
  'integration.sync.failed',
  'system.updated',
  'system.rollback',
]

const rules = ref<NotificationRule[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const creating = ref(false)
const testing = ref<Record<string, boolean>>({})

const form = reactive({
  name: '',
  event_name: 'acc.transaction.posted',
  chat_id: '',
  template: '',
})

async function load() {
  loading.value = true
  try {
    rules.value = await get<NotificationRule[]>('/integrations/notification-rules')
  } finally {
    loading.value = false
  }
}

function openDialog() {
  form.name = ''
  form.event_name = EVENTS[0]
  form.chat_id = ''
  form.template = t('notify.defaultTemplate')
  dialogVisible.value = true
}

async function createRule() {
  if (!form.name || !form.chat_id) {
    ElMessage.warning(t('connections.fieldRequired'))
    return
  }
  creating.value = true
  try {
    await post('/integrations/notification-rules', { ...form })
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('errors.unknown'))
  } finally {
    creating.value = false
  }
}

async function testRule(row: NotificationRule) {
  testing.value[row.id] = true
  try {
    const result = await post<{ ok: boolean }>(`/integrations/notification-rules/${row.id}/test`)
    if (result.ok) {
      ElMessage.success(t('notify.testSent'))
    } else {
      ElMessage.error(t('notify.testFailed'))
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('errors.unknown'))
  } finally {
    testing.value[row.id] = false
  }
}

async function removeRule(row: NotificationRule) {
  await del(`/integrations/notification-rules/${row.id}`)
  await load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page__header">
      <h2 class="page__title">{{ t('notify.title') }}</h2>
      <el-button type="primary" :icon="Bell" @click="openDialog">
        {{ t('notify.create') }}
      </el-button>
    </div>

    <el-table :data="rules" v-loading="loading">
      <el-table-column prop="name" :label="t('notify.name')" min-width="140" />
      <el-table-column prop="event_name" :label="t('notify.event')" min-width="200" />
      <el-table-column prop="chat_id" :label="t('notify.chatId')" width="120" />
      <el-table-column prop="template" :label="t('notify.template')" min-width="220" show-overflow-tooltip />
      <el-table-column :label="t('notify.active')" width="80">
        <template #default="{ row }">
          {{ row.is_active ? t('connections.yes') : t('connections.no') }}
        </template>
      </el-table-column>
      <el-table-column width="200">
        <template #default="{ row }">
          <el-button size="small" :icon="Position" :loading="testing[row.id]" @click="testRule(row)">
            {{ t('notify.test') }}
          </el-button>
          <el-button size="small" :icon="Delete" @click="removeRule(row)" />
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="t('notify.create')" width="560px">
      <el-form label-position="top">
        <el-form-item :label="t('notify.name')" required>
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item :label="t('notify.event')" required>
          <el-select v-model="form.event_name" style="width: 100%">
            <el-option v-for="event in EVENTS" :key="event" :value="event" :label="event" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('notify.chatId')" required>
          <el-input v-model="form.chat_id" placeholder="-1001234567890" />
        </el-form-item>
        <el-form-item :label="t('notify.template')">
          <el-input v-model="form.template" type="textarea" :rows="3" />
          <div class="muted">{{ t('notify.templateHint') }}</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">{{ t('connections.cancel') }}</el-button>
        <el-button type="primary" :loading="creating" @click="createRule">
          {{ t('connections.save') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

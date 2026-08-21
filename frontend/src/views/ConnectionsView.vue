<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, Link, Plus } from '@element-plus/icons-vue'
import { get, post } from '../api/client'
import type { Connection, ConnectorType, TestResult, Webhook, WebhookCreated } from '../api/types'

const { t } = useI18n()

// ----- Подключения -----
const connections = ref<Connection[]>([])
const connectors = ref<ConnectorType[]>([])
const connectorNames = computed(() =>
  Object.fromEntries(connectors.value.map((c) => [c.code, c.display_name])),
)

const testing = ref<Record<string, boolean>>({})
const testResults = ref<Record<string, TestResult>>({})

async function loadConnections() {
  connections.value = await get<Connection[]>('/integrations/connections')
}

async function loadConnectors() {
  connectors.value = await get<ConnectorType[]>('/integrations/connectors')
}

async function testConnection(row: Connection) {
  testing.value[row.id] = true
  try {
    testResults.value[row.id] = await post<TestResult>(`/integrations/connections/${row.id}/test`)
    const result = testResults.value[row.id]
    if (result.ok) {
      ElMessage.success(t('connections.testOk'))
    } else {
      ElMessage.error(`${t('connections.testFailed')}: ${result.error}`)
    }
    await loadConnections()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('errors.unknown'))
  } finally {
    testing.value[row.id] = false
  }
}

// ----- Диалог создания: поля config по config_schema выбранного типа -----
const dialogVisible = ref(false)
const creating = ref(false)
const form = reactive({
  name: '',
  connectorCode: '',
  secret: '',
  config: {} as Record<string, string | number>,
})

const selectedType = computed(() =>
  connectors.value.find((c) => c.code === form.connectorCode),
)

function openCreateDialog() {
  form.name = ''
  form.connectorCode = ''
  form.secret = ''
  form.config = {}
  dialogVisible.value = true
}

function onTypeChange() {
  form.config = {}
  for (const [field, schema] of Object.entries(selectedType.value?.config_schema ?? {})) {
    form.config[field] = (schema.default ?? (schema.type === 'int' ? 0 : '')) as string | number
  }
}

async function createConnection() {
  if (!form.name || !form.connectorCode) {
    ElMessage.warning(t('connections.fieldRequired'))
    return
  }
  creating.value = true
  try {
    await post<Connection>('/integrations/connections', {
      name: form.name,
      connector_code: form.connectorCode,
      credentials: form.secret ? { api_key: form.secret } : {},
      config: form.config,
    })
    dialogVisible.value = false
    await loadConnections()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('errors.unknown'))
  } finally {
    creating.value = false
  }
}

// ----- Webhooks -----
const webhooks = ref<Webhook[]>([])
const webhookName = ref('')
const creatingWebhook = ref(false)
const createdWebhook = ref<WebhookCreated | null>(null)

async function loadWebhooks() {
  webhooks.value = await get<Webhook[]>('/integrations/webhooks')
}

function fullUrl(hook: Webhook | WebhookCreated): string {
  return `${window.location.origin}${hook.url_path}`
}

async function copyToClipboard(value: string) {
  try {
    await navigator.clipboard.writeText(value)
    ElMessage.success(t('connections.copied'))
  } catch {
    ElMessage.error(t('connections.copyFailed'))
  }
}

async function createWebhook() {
  if (!webhookName.value) {
    ElMessage.warning(t('connections.fieldRequired'))
    return
  }
  creatingWebhook.value = true
  try {
    createdWebhook.value = await post<WebhookCreated>('/integrations/webhooks', {
      name: webhookName.value,
    })
    webhookName.value = ''
    await loadWebhooks()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('errors.unknown'))
  } finally {
    creatingWebhook.value = false
  }
}

function showTokenHint() {
  ElMessageBox.alert(t('connections.tokenHint'), t('connections.tokenHintTitle'), {
    confirmButtonText: 'OK',
    type: 'warning',
  })
}

onMounted(async () => {
  await Promise.all([loadConnections(), loadConnectors(), loadWebhooks()])
})
</script>

<template>
  <div class="page">
    <el-tabs>
      <el-tab-pane :label="t('connections.tab')">
        <div class="page__header">
          <h2 class="page__title">{{ t('connections.title') }}</h2>
          <el-button type="primary" :icon="Plus" @click="openCreateDialog">
            {{ t('connections.create') }}
          </el-button>
        </div>

        <el-table :data="connections">
          <el-table-column prop="name" :label="t('connections.name')" min-width="180" />
          <el-table-column :label="t('connections.type')" min-width="160">
            <template #default="{ row }">
              {{ connectorNames[row.connector_code] ?? row.connector_code }}
            </template>
          </el-table-column>
          <el-table-column :label="t('connections.status')" width="120">
            <template #default="{ row }">
              <el-tag v-if="row.last_check_ok === true" type="success">
                {{ t('connections.statusOk') }}
              </el-tag>
              <el-tag v-else-if="row.last_check_ok === false" type="danger">
                {{ t('connections.statusError') }}
              </el-tag>
              <span v-else>{{ t('connections.statusUnknown') }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('connections.active')" width="90">
            <template #default="{ row }">
              {{ row.is_active ? t('connections.yes') : t('connections.no') }}
            </template>
          </el-table-column>
          <el-table-column width="200">
            <template #default="{ row }">
              <el-button
                size="small"
                :loading="testing[row.id]"
                @click="testConnection(row)"
              >
                {{ testing[row.id] ? t('connections.testing') : t('connections.test') }}
              </el-button>
            </template>
          </el-table-column>
          <template #empty>
            {{ t('connections.empty') }}
          </template>
        </el-table>
      </el-tab-pane>

      <el-tab-pane :label="t('connections.webhooks')">
        <div class="page__header">
          <h2 class="page__title">{{ t('connections.webhooks') }}</h2>
          <div class="webhook-create">
            <el-input
              v-model="webhookName"
              :placeholder="t('connections.webhookName')"
              style="width: 240px"
              @keyup.enter="createWebhook"
            />
            <el-button type="primary" :loading="creatingWebhook" @click="createWebhook">
              {{ t('connections.createWebhook') }}
            </el-button>
          </div>
        </div>

        <el-table :data="webhooks">
          <el-table-column prop="name" :label="t('connections.webhookName')" min-width="160" />
          <el-table-column :label="t('connections.url')" min-width="320">
            <template #default="{ row }">
              <span class="webhook-url">{{ fullUrl(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column width="320">
            <template #default="{ row }">
              <el-button size="small" :icon="CopyDocument" @click="copyToClipboard(fullUrl(row))">
                {{ t('connections.copyUrl') }}
              </el-button>
              <el-button size="small" :icon="Link" @click="showTokenHint">
                {{ t('connections.tokenOnce') }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- Диалог создания подключения -->
    <el-dialog v-model="dialogVisible" :title="t('connections.dialogTitle')" width="560px">
      <el-form label-position="top">
        <el-form-item :label="t('connections.name')" required>
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item :label="t('connections.selectType')" required>
          <el-select
            v-model="form.connectorCode"
            :placeholder="t('connections.selectTypePlaceholder')"
            style="width: 100%"
            @change="onTypeChange"
          >
            <el-option
              v-for="connector in connectors"
              :key="connector.code"
              :value="connector.code"
              :label="connector.display_name"
            />
          </el-select>
        </el-form-item>

        <!-- Поля config рендеруются по config_schema выбранного типа -->
        <el-form-item
          v-for="(schema, field) in selectedType?.config_schema ?? {}"
          :key="field"
          :label="field"
          :required="schema.required"
        >
          <el-select v-if="schema.type === 'enum'" v-model="form.config[field]" style="width: 100%">
            <el-option v-for="value in schema.values ?? []" :key="value" :value="value" :label="value" />
          </el-select>
          <el-input-number
            v-else-if="schema.type === 'int'"
            v-model="form.config[field]"
            style="width: 100%"
          />
          <el-input v-else v-model="form.config[field]" />
        </el-form-item>

        <el-form-item v-if="form.connectorCode" :label="t('connections.secret')">
          <el-input
            v-model="form.secret"
            type="password"
            show-password
            :placeholder="t('connections.secretPlaceholder')"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">{{ t('connections.cancel') }}</el-button>
        <el-button type="primary" :loading="creating" @click="createConnection">
          {{ t('connections.save') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- Токен показывается ровно один раз, сразу после создания -->
    <el-dialog
      :model-value="createdWebhook !== null"
      :title="t('connections.tokenDialogTitle')"
      width="560px"
      @close="createdWebhook = null"
    >
      <template v-if="createdWebhook">
        <p>{{ t('connections.url') }}:</p>
        <div class="token-box">
          <code>{{ fullUrl(createdWebhook) }}</code>
          <el-button :icon="CopyDocument" size="small" @click="copyToClipboard(fullUrl(createdWebhook))" />
        </div>
        <p>{{ t('connections.tokenLabel') }}:</p>
        <div class="token-box">
          <code>{{ createdWebhook.secret_token }}</code>
          <el-button
            :icon="CopyDocument"
            size="small"
            @click="copyToClipboard(createdWebhook.secret_token)"
          />
        </div>
        <el-alert :title="t('connections.tokenWarning')" type="warning" show-icon :closable="false" />
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.webhook-create {
  display: flex;
  gap: 8px;
}

.webhook-url {
  font-size: 12px;
  color: #606266;
  word-break: break-all;
}

.token-box {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.token-box code {
  flex: 1;
  padding: 6px 8px;
  background: #f5f7fa;
  border-radius: 4px;
  word-break: break-all;
}
</style>

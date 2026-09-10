<script setup lang="ts">
// Подключения и вебхуки (этап G — переприход со старого Element Plus экрана
// на собственную ui-библиотеку; функциональность прежняя: реестр, проверка
// связи, создание по config_schema, вебхуки с одноразовым токеном).
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Copy, Link2, Plus } from 'lucide-vue-next'
import { get, post } from '../api/client'
import type { Connection, ConnectorType, TestResult, Webhook, WebhookCreated } from '../api/types'
import {
  Badge, Button, Card, CardContent, Dialog, EmptyState, Input, Label,
  Select, Skeleton, Tabs, useToast,
} from '../components/ui'

const { t, d } = useI18n()
const toast = useToast()

const tab = ref('connections')
const loading = ref(true)
// локальный поиск по справочникам (гейт 1.1a)
const connSearch = ref('')
const hookSearch = ref('')
const filteredConnections = computed(() => {
  const q = connSearch.value.trim().toLowerCase()
  return q
    ? connections.value.filter((c) => c.name.toLowerCase().includes(q) || c.connector_code.toLowerCase().includes(q))
    : connections.value
})
const filteredWebhooks = computed(() => {
  const q = hookSearch.value.trim().toLowerCase()
  return q
    ? webhooks.value.filter((w) => w.name.toLowerCase().includes(q) || fullUrl(w).toLowerCase().includes(q))
    : webhooks.value
})

// ----- Подключения -----
const connections = ref<Connection[]>([])
const connectors = ref<ConnectorType[]>([])
const connectorNames = computed(() =>
  Object.fromEntries(connectors.value.map((c) => [c.code, c.display_name])))
const testing = ref<Record<string, boolean>>({})

async function loadConnections() {
  connections.value = await get<Connection[]>('/integrations/connections')
}

async function testConnection(row: Connection) {
  testing.value[row.id] = true
  try {
    const result = await post<TestResult>(`/integrations/connections/${row.id}/test`)
    if (result.ok) toast.success(t('connections.testOk'))
    else toast.error(`${t('connections.testFailed')}: ${result.error}`)
    await loadConnections()
  } catch (error) {
    toast.apiError(error)
  } finally {
    testing.value[row.id] = false
  }
}

// ----- Диалог создания -----
const dialogVisible = ref(false)
const creating = ref(false)
const form = reactive({
  name: '',
  connectorCode: '',
  secret: '',
  config: {} as Record<string, string | number>,
})

const selectedType = computed(() =>
  connectors.value.find((c) => c.code === form.connectorCode))

function onTypeChange() {
  form.config = {}
  for (const [field, schema] of Object.entries(selectedType.value?.config_schema ?? {})) {
    form.config[field] = (schema.default ?? (schema.type === 'int' ? 0 : '')) as string | number
  }
}

async function createConnection() {
  if (creating.value) return
  if (!form.name.trim() || !form.connectorCode) {
    toast.error(t('connections.fieldRequired'))
    return
  }
  creating.value = true
  try {
    await post('/integrations/connections', {
      name: form.name.trim(),
      connector_code: form.connectorCode,
      credentials: form.secret ? { api_key: form.secret } : {},
      config: form.config,
    })
    toast.success(t('connections.created'))
    dialogVisible.value = false
    await loadConnections()
  } catch (error) {
    toast.apiError(error)
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
    toast.success(t('connections.copied'))
  } catch {
    toast.error(t('connections.copyFailed'))
  }
}

async function createWebhook() {
  if (creatingWebhook.value) return
  if (!webhookName.value.trim()) {
    toast.error(t('connections.fieldRequired'))
    return
  }
  creatingWebhook.value = true
  try {
    createdWebhook.value = await post<WebhookCreated>('/integrations/webhooks', {
      name: webhookName.value.trim(),
    })
    webhookName.value = ''
    await loadWebhooks()
  } catch (error) {
    toast.apiError(error)
  } finally {
    creatingWebhook.value = false
  }
}

onMounted(async () => {
  try {
    await Promise.all([
      loadConnections(),
      get<ConnectorType[]>('/integrations/connectors').then((data) => { connectors.value = data }),
      loadWebhooks(),
    ])
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="space-y-4">
    <Tabs
      :tabs="[
        { key: 'connections', label: t('connections.tab') },
        { key: 'webhooks', label: t('connections.webhooks') },
      ]"
      :model-value="tab"
      @update:model-value="tab = $event"
    />

    <!-- Подключения -->
    <div v-if="tab === 'connections'" class="space-y-4">
      <div class="flex items-center justify-between">
        <p class="text-base font-semibold">{{ t('connections.title') }}</p>
        <Input v-model="connSearch" :placeholder="t('ui.searchPlaceholder')" class="h-8 w-[220px]" />
        <Button variant="emerald" size="sm" class="gap-1.5" @click="dialogVisible = true">
          <Plus class="h-4 w-4" /> {{ t('connections.create') }}
        </Button>
      </div>
      <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="p-0">
          <div v-if="loading" class="space-y-2 p-4">
            <Skeleton class="h-10 w-full" />
            <Skeleton class="h-10 w-full" />
          </div>
          <div v-else-if="connections.length === 0" class="p-6">
            <EmptyState :title="t('ui.emptyTitle')" :description="t('connections.empty')" />
          </div>
          <div v-else class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                  <th class="px-3 py-2 font-medium">{{ t('connections.name') }}</th>
                  <th class="px-3 py-2 font-medium">{{ t('connections.type') }}</th>
                  <th class="px-3 py-2 font-medium">{{ t('connections.status') }}</th>
                  <th class="hidden px-3 py-2 font-medium sm:table-cell">{{ t('connections.active') }}</th>
                  <th class="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in filteredConnections" :key="row.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
                  <td class="px-3 py-2 font-medium">{{ row.name }}</td>
                  <td class="px-3 py-2 text-muted-foreground">{{ connectorNames[row.connector_code] ?? row.connector_code }}</td>
                  <td class="px-3 py-2">
                    <Badge v-if="row.last_check_ok === true" class="bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300">
                      {{ t('connections.statusOk') }}
                    </Badge>
                    <Badge v-else-if="row.last_check_ok === false" class="bg-red-100 text-red-700 dark:bg-red-950/60 dark:text-red-300">
                      {{ t('connections.statusError') }}
                    </Badge>
                    <span v-else class="text-muted-foreground">{{ t('connections.statusUnknown') }}</span>
                  </td>
                  <td class="hidden px-3 py-2 text-muted-foreground sm:table-cell">
                    {{ row.is_active ? t('connections.yes') : t('connections.no') }}
                  </td>
                  <td class="px-3 py-2 text-right">
                    <Button
                      variant="outline" size="sm" :disabled="testing[row.id]"
                      @click="testConnection(row)"
                    >
                      {{ testing[row.id] ? t('connections.testing') : t('connections.test') }}
                    </Button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>

    <!-- Вебхуки -->
    <div v-else class="space-y-4">
      <div class="flex items-center justify-between">
        <p class="text-base font-semibold">{{ t('connections.webhooks') }}</p>
        <Input v-model="hookSearch" :placeholder="t('ui.searchPlaceholder')" class="h-8 w-[220px]" />
      </div>
      <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="flex flex-col gap-2 p-4 sm:flex-row">
          <Input
            v-model="webhookName" :placeholder="t('connections.webhookName')"
            class="flex-1" @keyup.enter="createWebhook"
          />
          <Button variant="emerald" size="sm" :disabled="creatingWebhook" @click="createWebhook">
            {{ t('connections.createWebhook') }}
          </Button>
        </CardContent>
      </Card>
      <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="p-0">
          <div v-if="loading" class="space-y-2 p-4"><Skeleton class="h-10 w-full" /></div>
          <div v-else-if="webhooks.length === 0" class="p-6">
            <EmptyState :title="t('ui.emptyTitle')" :description="t('connections.empty')" />
          </div>
          <div v-else class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                  <th class="px-3 py-2 font-medium">{{ t('connections.webhookName') }}</th>
                  <th class="px-3 py-2 font-medium">{{ t('connections.url') }}</th>
                  <th class="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in filteredWebhooks" :key="row.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
                  <td class="px-3 py-2 font-medium">{{ row.name }}</td>
                  <td class="max-w-[340px] px-3 py-2 font-mono text-xs text-muted-foreground">{{ fullUrl(row) }}</td>
                  <td class="px-3 py-2 text-right">
                    <div class="flex justify-end gap-1">
                      <Button variant="ghost" size="icon" class="h-7 w-7" :title="t('connections.copyUrl')" @click="copyToClipboard(fullUrl(row))">
                        <Copy class="h-3.5 w-3.5" />
                      </Button>
                      <Button variant="ghost" size="icon" class="h-7 w-7" :title="t('connections.tokenOnce')" @click="toast.success(t('connections.tokenHintTitle'), t('connections.tokenHint'))">
                        <Link2 class="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>

    <!-- Диалог создания подключения -->
    <Dialog v-model:open="dialogVisible" :title="t('connections.dialogTitle')" width="560px">
      <form class="space-y-4" @submit.prevent="createConnection">
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('connections.name') }}</Label>
          <Input v-model="form.name" />
        </div>
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('connections.selectType') }}</Label>
          <Select
            v-model="form.connectorCode"
            :options="connectors.map((c) => ({ value: c.code, label: c.display_name }))"
            @update:model-value="onTypeChange"
          />
        </div>
        <!-- поля config по config_schema выбранного типа -->
        <div v-for="(schema, field) in selectedType?.config_schema ?? {}" :key="field" class="space-y-1.5">
          <Label class="text-xs font-medium">{{ field }}</Label>
          <Select
            v-if="schema.type === 'enum'" :model-value="String(form.config[field])"
            :options="(schema.values ?? []).map((value: string) => ({ value, label: value }))"
            @update:model-value="form.config[field] = $event"
          />
          <Input
            v-else-if="schema.type === 'int'" :model-value="String(form.config[field])" type="number"
            @update:model-value="form.config[field] = Number($event || 0)"
          />
          <Input
            v-else :model-value="String(form.config[field])"
            @update:model-value="form.config[field] = $event"
          />
        </div>
        <div v-if="form.connectorCode" class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('connections.secret') }}</Label>
          <Input v-model="form.secret" type="password" :placeholder="t('connections.secretPlaceholder')" />
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="dialogVisible = false">{{ t('connections.cancel') }}</Button>
          <Button variant="emerald" type="submit" size="sm" :disabled="creating">
            {{ t('connections.save') }}
          </Button>
        </div>
      </form>
    </Dialog>

    <!-- Токен вебхука — ровно один раз -->
    <Dialog
      :open="createdWebhook !== null" :title="t('connections.tokenDialogTitle')"
      @update:open="(v: boolean) => { if (!v) createdWebhook = null }"
    >
      <div v-if="createdWebhook" class="space-y-3">
        <div>
          <p class="text-xs text-muted-foreground">{{ t('connections.url') }}</p>
          <div class="mt-1 flex items-center gap-2 rounded-lg bg-zinc-100 p-2 dark:bg-zinc-800">
            <code class="flex-1 break-all text-xs">{{ fullUrl(createdWebhook) }}</code>
            <Button variant="outline" size="sm" @click="copyToClipboard(fullUrl(createdWebhook))">
              <Copy class="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
        <div>
          <p class="text-xs text-muted-foreground">{{ t('connections.tokenLabel') }}</p>
          <div class="mt-1 flex items-center gap-2 rounded-lg bg-zinc-100 p-2 dark:bg-zinc-800">
            <code class="flex-1 break-all text-xs">{{ createdWebhook.secret_token }}</code>
            <Button variant="outline" size="sm" @click="copyToClipboard(createdWebhook.secret_token)">
              <Copy class="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
        <p class="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
          {{ t('connections.tokenWarning') }}
        </p>
        <div class="flex justify-end">
          <Button variant="outline" size="sm" @click="createdWebhook = null">{{ t('security.done') }}</Button>
        </div>
      </div>
    </Dialog>
  </div>
</template>

<script setup lang="ts">
// Правила уведомлений (этап G — переприход на ui-библиотеку): список,
// тест-отправка, удаление, создание с шаблоном.
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Plus, Send, Trash2 } from 'lucide-vue-next'
import { del, get, post } from '../api/client'
import {
  Button, Card, CardContent, Dialog, EmptyState, Input, Label,
  Select, Skeleton, useToast,
} from '../components/ui'

const { t } = useI18n()
const toast = useToast()

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
  'acc.purchase.received',
  'acc.sales.order.confirmed',
  'acc.sales.shipped',
  'acc.production.order.posted',
]

const rules = ref<NotificationRule[]>([])
const loading = ref(true)
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
  if (creating.value) return
  if (!form.name || !form.chat_id) {
    toast.error(t('connections.fieldRequired'))
    return
  }
  creating.value = true
  try {
    await post('/integrations/notification-rules', { ...form })
    toast.success(t('notify.created'))
    dialogVisible.value = false
    await load()
  } catch (error) {
    toast.apiError(error)
  } finally {
    creating.value = false
  }
}

async function testRule(row: NotificationRule) {
  testing.value[row.id] = true
  try {
    const result = await post<{ ok: boolean }>(`/integrations/notification-rules/${row.id}/test`)
    if (result.ok) toast.success(t('notify.testSent'))
    else toast.error(t('notify.testFailed'))
  } catch (error) {
    toast.apiError(error)
  } finally {
    testing.value[row.id] = false
  }
}

async function removeRule(row: NotificationRule) {
  try {
    await del(`/integrations/notification-rules/${row.id}`)
    toast.success(t('notify.removed'))
    await load()
  } catch (error) {
    toast.apiError(error)
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <p class="text-base font-semibold">{{ t('notify.rulesTitle') }}</p>
      <Button variant="emerald" size="sm" class="gap-1.5" @click="openDialog">
        <Plus class="h-4 w-4" /> {{ t('notify.create') }}
      </Button>
    </div>

    <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-0">
        <div v-if="loading" class="space-y-2 p-4">
          <Skeleton class="h-10 w-full" />
          <Skeleton class="h-10 w-full" />
        </div>
        <div v-else-if="rules.length === 0" class="p-6">
          <EmptyState :title="t('ui.emptyTitle')" :description="t('notify.emptyRules')" />
        </div>
        <div v-else class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                <th class="px-3 py-2 font-medium">{{ t('notify.name') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('notify.event') }}</th>
                <th class="hidden px-3 py-2 font-medium sm:table-cell">{{ t('notify.chatId') }}</th>
                <th class="hidden px-3 py-2 font-medium lg:table-cell">{{ t('notify.template') }}</th>
                <th class="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in rules" :key="row.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
                <td class="px-3 py-2 font-medium">{{ row.name }}</td>
                <td class="px-3 py-2 font-mono text-xs text-muted-foreground">{{ row.event_name }}</td>
                <td class="hidden px-3 py-2 text-muted-foreground sm:table-cell">{{ row.chat_id }}</td>
                <td class="hidden max-w-[260px] truncate px-3 py-2 text-xs text-muted-foreground lg:table-cell">
                  {{ row.template }}
                </td>
                <td class="px-3 py-2">
                  <div class="flex justify-end gap-1">
                    <Button variant="ghost" size="icon" class="h-7 w-7" :title="t('notify.test')" @click="testRule(row)">
                      <Send class="h-3.5 w-3.5" />
                    </Button>
                    <Button variant="ghost" size="icon" class="h-7 w-7" :title="t('notify.remove')" @click="removeRule(row)">
                      <Trash2 class="h-3.5 w-3.5 text-red-500" />
                    </Button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>

    <Dialog v-model:open="dialogVisible" :title="t('notify.create')" width="560px">
      <form class="space-y-4" @submit.prevent="createRule">
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('notify.name') }}</Label>
          <Input v-model="form.name" />
        </div>
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('notify.event') }}</Label>
          <Select v-model="form.event_name" :options="EVENTS.map((event) => ({ value: event, label: event }))" />
        </div>
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('notify.chatId') }}</Label>
          <Input v-model="form.chat_id" placeholder="-1001234567890" />
        </div>
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('notify.template') }}</Label>
          <textarea
            v-model="form.template" rows="3"
            class="w-full rounded-lg border border-input bg-background px-3 py-2 text-sm shadow-sm"
          />
          <p class="text-xs text-muted-foreground">{{ t('notify.templateHint') }}</p>
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="dialogVisible = false">{{ t('connections.cancel') }}</Button>
          <Button variant="emerald" type="submit" size="sm" :disabled="creating">
            {{ t('connections.save') }}
          </Button>
        </div>
      </form>
    </Dialog>
  </div>
</template>

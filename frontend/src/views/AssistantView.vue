<script setup lang="ts">
// ИИ-ассистент (этап G — новый дизайн на ui-библиотеке): чаты с сессиями
// и источниками RAG, документы (загрузка/удаление), предложения с
// подтвердить/отклонить и автоприменением. Функциональность прежняя.
import { computed, nextTick, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Bot, Check, FileText, Plus, Send, Trash2, Upload, X } from 'lucide-vue-next'
import { del, get, post, put } from '../api/client'
import {
  Badge, Button, Card, CardContent, EmptyState, Input, Skeleton, Switch,
  Tabs, useToast,
} from '../components/ui'
import { useAuthStore } from '../stores/auth'

const { t, d } = useI18n()
const auth = useAuthStore()
const toast = useToast()
const canWrite = computed(() => auth.moduleLevel('ai') === 'rw')

const tab = ref('chat')

// ---------- Чаты ----------
interface Session { id: string; title: string; created_at: string }
interface Source { document_id: string; document_name: string; text: string }
interface Message { role: string; content: string; meta?: { sources?: Source[] } }

const sessions = ref<Session[]>([])
const activeId = ref<string | null>(null)
const messages = ref<Message[]>([])
const input = ref('')
const sending = ref(false)
const loadingSessions = ref(true)
const bottom = ref<HTMLElement>()

async function loadSessions() {
  loadingSessions.value = true
  try {
    sessions.value = await get<Session[]>('/ai/sessions')
  } finally {
    loadingSessions.value = false
  }
}

async function openSession(id: string) {
  activeId.value = id
  messages.value = await get<Message[]>(`/ai/sessions/${id}`)
  scrollToBottom()
}

async function sendMessage() {
  const text = input.value.trim()
  if (!text || sending.value || !canWrite.value) return
  sending.value = true
  input.value = ''
  messages.value.push({ role: 'user', content: text })
  scrollToBottom()
  try {
    const result = await post<{ session_id: string; answer: string; sources: Source[] }>(
      '/ai/chat', { session_id: activeId.value, message: text })
    activeId.value = result.session_id
    messages.value.push({
      role: 'assistant',
      content: result.answer,
      meta: { sources: result.sources },
    })
    await loadSessions()
  } catch (error) {
    toast.apiError(error)
  } finally {
    sending.value = false
    scrollToBottom()
  }
}

async function removeSession(id: string) {
  try {
    await del(`/ai/sessions/${id}`)
    if (activeId.value === id) {
      activeId.value = null
      messages.value = []
    }
    await loadSessions()
  } catch (error) {
    toast.apiError(error)
  }
}

function newSession() {
  activeId.value = null
  messages.value = []
}

function scrollToBottom() {
  nextTick(() => bottom.value?.scrollIntoView({ behavior: 'smooth' }))
}

// ---------- Документы ----------
interface Document { id: string; name: string; created_at: string }
const documents = ref<Document[]>([])
const uploading = ref(false)
const fileInput = ref<HTMLInputElement>()

async function loadDocuments() {
  try {
    documents.value = await get<Document[]>('/ai/documents')
  } catch {
    documents.value = []
  }
}

function pickFile() {
  fileInput.value?.click()
}

async function uploadFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file || uploading.value) return
  uploading.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    const response = await fetch('/api/v1/ai/documents', {
      method: 'POST',
      headers: { Authorization: `Bearer ${auth.accessToken}` },
      body: form,
    })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    toast.success(t('ai.uploaded'))
    await loadDocuments()
  } catch (error) {
    toast.apiError(error)
  } finally {
    uploading.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

async function removeDocument(id: string) {
  try {
    await del(`/ai/documents/${id}`)
    await loadDocuments()
  } catch (error) {
    toast.apiError(error)
  }
}

// ---------- Предложения ----------
interface Proposal {
  id: string
  action_type: string
  payload: Record<string, unknown>
  reason: string
  status: string
  result: Record<string, unknown>
  created_at: string
}
const proposals = ref<Proposal[]>([])
const autopapply = ref(false)
const deciding = ref<Record<string, boolean>>({})

async function loadProposals() {
  try {
    proposals.value = await get<Proposal[]>('/ai/proposals')
  } catch {
    proposals.value = []
  }
}

async function loadSettings() {
  try {
    const s = await get<{ autopapply: boolean }>('/ai/settings')
    autopapply.value = s.autopapply
  } catch { /* настройки недоступны — дефолт */ }
}

async function toggleAutopapply(value: boolean) {
  try {
    await put('/ai/settings', { autopapply: value })
    autopapply.value = value
  } catch (error) {
    toast.apiError(error)
  }
}

async function decide(row: Proposal, action: 'approve' | 'reject') {
  deciding.value[row.id] = true
  try {
    await post(`/ai/proposals/${row.id}/${action}`)
    toast.success(action === 'approve' ? t('ai.approved') : t('ai.rejected'))
    await loadProposals()
  } catch (error) {
    toast.apiError(error)
  } finally {
    deciding.value[row.id] = false
  }
}

function proposalTone(status: string): string {
  if (status === 'approved' || status === 'auto_applied') {
    return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300'
  }
  if (status === 'rejected' || status === 'failed') {
    return 'bg-red-100 text-red-700 dark:bg-red-950/60 dark:text-red-300'
  }
  return 'bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300'
}

onMounted(() => {
  void loadSessions()
  void loadDocuments()
  void loadProposals()
  void loadSettings()
})
</script>

<template>
  <div class="space-y-4">
    <Tabs
      :tabs="[
        { key: 'chat', label: t('ai.tabChat') },
        { key: 'documents', label: t('ai.tabDocuments') },
        { key: 'proposals', label: t('ai.tabProposals') },
      ]"
      :model-value="tab"
      @update:model-value="tab = $event"
    />

    <!-- Чат -->
    <div v-if="tab === 'chat'" class="grid grid-cols-1 gap-4 lg:grid-cols-[240px_1fr]">
      <Card class="h-fit border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="p-3">
          <Button v-if="canWrite" variant="outline" size="sm" class="mb-2 w-full gap-1.5" @click="newSession">
            <Plus class="h-3.5 w-3.5" /> {{ t('ai.newSession') }}
          </Button>
          <div v-if="loadingSessions" class="space-y-2">
            <Skeleton class="h-8 w-full" />
            <Skeleton class="h-8 w-full" />
          </div>
          <ul v-else class="space-y-0.5">
            <li
              v-for="session in sessions" :key="session.id"
              class="group flex cursor-pointer items-center justify-between gap-1 rounded-lg px-2 py-1.5 text-sm transition-colors hover:bg-accent"
              :class="session.id === activeId && 'bg-emerald-50/70 text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-300'"
              @click="openSession(session.id)"
            >
              <span class="min-w-0 flex-1 truncate">{{ session.title || t('ai.untitled') }}</span>
              <button
                v-if="canWrite" type="button"
                class="shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 hover:text-red-500"
                :title="t('ai.deleteSession')" @click.stop="removeSession(session.id)"
              >
                <Trash2 class="h-3.5 w-3.5" />
              </button>
            </li>
            <li v-if="!sessions.length" class="px-2 py-1 text-xs text-muted-foreground">{{ t('ai.noSessions') }}</li>
          </ul>
        </CardContent>
      </Card>

      <Card class="flex min-h-[440px] flex-col border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="flex flex-1 flex-col p-0">
          <div class="erp-scroll flex-1 space-y-3 overflow-y-auto p-4">
            <div
              v-for="(message, index) in messages" :key="index"
              class="flex flex-col" :class="message.role === 'user' && 'items-end'"
            >
              <div
                class="max-w-[75%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-sm leading-relaxed"
                :class="message.role === 'user'
                  ? 'bg-emerald-600 text-white'
                  : 'bg-zinc-100 dark:bg-zinc-800'"
              >{{ message.content }}</div>
              <div v-if="message.meta?.sources?.length" class="mt-1 max-w-[75%] space-y-0.5">
                <p class="text-[11px] text-muted-foreground">{{ t('ai.sources') }}:</p>
                <p v-for="(source, sIndex) in message.meta.sources" :key="sIndex" class="flex items-center gap-1 text-[11px] text-muted-foreground">
                  <FileText class="h-3 w-3" /> {{ source.document_name }}
                </p>
              </div>
            </div>
            <div v-if="!messages.length" class="flex flex-1 items-center justify-center py-16">
              <div class="text-center">
                <span class="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-violet-500/10 text-violet-600 dark:text-violet-300">
                  <Bot class="h-6 w-6" />
                </span>
                <p class="mt-3 text-sm text-muted-foreground">{{ t('ai.placeholder') }}</p>
              </div>
            </div>
            <div ref="bottom" />
          </div>
          <form
            class="flex gap-2 border-t border-zinc-100 p-3 dark:border-zinc-800"
            @submit.prevent="sendMessage"
          >
            <Input
              v-model="input" :placeholder="t('ai.placeholder')" :disabled="sending || !canWrite"
              class="flex-1"
            />
            <Button variant="emerald" size="icon" type="submit" :disabled="sending || !input.trim() || !canWrite">
              <Send class="h-4 w-4" />
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>

    <!-- Документы -->
    <Card v-else-if="tab === 'documents'" class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-0">
        <div class="flex items-center justify-between px-4 py-3">
          <p class="text-sm font-semibold">{{ t('ai.documents') }}</p>
          <div v-if="canWrite">
            <input ref="fileInput" type="file" class="hidden" @change="uploadFile">
            <Button variant="emerald" size="sm" class="gap-1.5" :disabled="uploading" @click="pickFile">
              <Upload class="h-3.5 w-3.5" /> {{ uploading ? t('ai.uploading') : t('ai.upload') }}
            </Button>
          </div>
        </div>
        <div v-if="documents.length === 0" class="p-6">
          <EmptyState :title="t('ui.emptyTitle')" :description="t('ai.noDocuments')" />
        </div>
        <div v-else class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                <th class="px-3 py-2 font-medium">{{ t('ai.documentName') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('ai.documentDate') }}</th>
                <th v-if="canWrite" class="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              <tr v-for="doc in documents" :key="doc.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
                <td class="px-3 py-2 font-medium">
                  <span class="flex items-center gap-2"><FileText class="h-3.5 w-3.5 text-muted-foreground" /> {{ doc.name }}</span>
                </td>
                <td class="px-3 py-2 text-xs text-muted-foreground">{{ d(doc.created_at, 'short') }}</td>
                <td v-if="canWrite" class="px-3 py-2 text-right">
                  <Button variant="ghost" size="icon" class="h-7 w-7" :title="t('ai.deleteDocument')" @click="removeDocument(doc.id)">
                    <Trash2 class="h-3.5 w-3.5 text-red-500" />
                  </Button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>

    <!-- Предложения -->
    <div v-else class="space-y-4">
      <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="flex flex-wrap items-center justify-between gap-3 p-5">
          <div class="flex items-center gap-3">
            <Switch :model-value="autopapply" :disabled="!canWrite" @update:model-value="toggleAutopapply($event)" />
            <div>
              <p class="text-sm font-medium">{{ t('ai.autopapply') }}</p>
              <p class="text-xs text-muted-foreground">{{ t('ai.autopapplyHint') }}</p>
            </div>
          </div>
          <Button variant="ghost" size="sm" @click="loadProposals">{{ t('sync.refresh') }}</Button>
        </CardContent>
      </Card>
      <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="p-0">
          <div v-if="proposals.length === 0" class="p-6">
            <EmptyState :title="t('ui.emptyTitle')" :description="t('ai.noProposals')" />
          </div>
          <div v-else class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                  <th class="px-3 py-2 font-medium">{{ t('ai.proposalType') }}</th>
                  <th class="px-3 py-2 font-medium">{{ t('ai.proposalPayload') }}</th>
                  <th class="hidden px-3 py-2 font-medium md:table-cell">{{ t('ai.proposalReason') }}</th>
                  <th class="px-3 py-2 font-medium">{{ t('ai.proposalStatus') }}</th>
                  <th v-if="canWrite" class="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in proposals" :key="row.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
                  <td class="whitespace-nowrap px-3 py-2 font-medium">{{ t(`ai.action.${row.action_type}`) }}</td>
                  <td class="max-w-[260px] px-3 py-2 text-xs text-muted-foreground">
                    {{ row.payload.kind }} · {{ row.payload.amount }} {{ row.payload.currency }} · {{ row.payload.description }}
                  </td>
                  <td class="hidden max-w-[200px] truncate px-3 py-2 text-xs text-muted-foreground md:table-cell">{{ row.reason }}</td>
                  <td class="px-3 py-2">
                    <Badge :class="proposalTone(row.status)">{{ t(`ai.status.${row.status}`) }}</Badge>
                  </td>
                  <td v-if="canWrite" class="px-3 py-2 text-right">
                    <div v-if="row.status === 'pending'" class="flex justify-end gap-1">
                      <Button
                        variant="outline" size="icon" class="h-7 w-7 border-emerald-300 text-emerald-600 dark:border-emerald-800 dark:text-emerald-400"
                        :disabled="deciding[row.id]" :title="t('ai.approve')" @click="decide(row, 'approve')"
                      >
                        <Check class="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        variant="outline" size="icon" class="h-7 w-7 border-red-300 text-red-600 dark:border-red-900 dark:text-red-400"
                        :disabled="deciding[row.id]" :title="t('ai.reject')" @click="decide(row, 'reject')"
                      >
                        <X class="h-3.5 w-3.5" />
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
  </div>
</template>

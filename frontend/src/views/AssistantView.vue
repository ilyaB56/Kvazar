<script setup lang="ts">
// ИИ-ассистент (этап G — новый дизайн на ui-библиотеке): чаты с сессиями
// и источниками RAG, документы (загрузка/удаление), предложения с
// подтвердить/отклонить и автоприменением. Функциональность прежняя.
// Списки сессий/документов/предложений пагинированы (PaginatedList, по 50);
// сообщения чата грузятся целиком по открытой сессии.
import { computed, nextTick, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Bot, Check, Download, FileText, Plus, Send, Trash2, Upload, X } from 'lucide-vue-next'
import { del, get, post, put } from '../api/client'
import {
  Badge, Button, Card, CardContent, EmptyState, Input, PaginatedList, Skeleton, Switch,
  Tabs, useToast,
} from '../components/ui'
import type { PageOf } from '../components/ui'
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

const sessionsList = ref<{ reload: () => Promise<void> } | null>(null)
const activeId = ref<string | null>(null)
const messages = ref<Message[]>([])
const input = ref('')
const sending = ref(false)
// ответ ещё может прийти поллингом после обрыва запроса (медленная LLM)
const polling = ref(false)
const bottom = ref<HTMLElement>()

function fetchSessionsPage(offset: number, limit: number) {
  return get<PageOf<Session>>(`/ai/sessions?limit=${limit}&offset=${offset}`)
}

async function openSession(id: string) {
  activeId.value = id
  messages.value = await get<Message[]>(`/ai/sessions/${id}`)
  scrollToBottom('auto')
}

async function sendMessage() {
  const text = input.value.trim()
  if (!text || sending.value || polling.value || !canWrite.value) return
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
    await sessionsList.value?.reload()
  } catch (error) {
    toast.apiError(error)
    // Генерация на локальной LLM идёт минуты: прокси/сеть могли оборвать
    // запрос, а бэкенд допишет ответ в сессию — поллим, чтобы показать
    // его без перезагрузки страницы.
    void pollForAnswer()
  } finally {
    sending.value = false
    scrollToBottom()
  }
}

async function pollForAnswer() {
  const sessionId = activeId.value
  if (!sessionId) return
  polling.value = true
  const known = messages.value.length
  try {
    for (let attempt = 0; attempt < 24; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 5000))
      if (activeId.value !== sessionId) return
      try {
        const fresh = await get<Message[]>(`/ai/sessions/${sessionId}`)
        if (fresh.length > known) {
          messages.value = fresh
          await sessionsList.value?.reload()
          return
        }
      } catch { /* сеть моргнула — попробуем на следующей итерации */ }
    }
  } finally {
    polling.value = false
  }
}

async function removeSession(id: string) {
  try {
    await del(`/ai/sessions/${id}`)
    if (activeId.value === id) {
      activeId.value = null
      messages.value = []
    }
    await sessionsList.value?.reload()
  } catch (error) {
    toast.apiError(error)
  }
}

function newSession() {
  activeId.value = null
  messages.value = []
}

function scrollToBottom(behavior: ScrollBehavior = 'smooth') {
  nextTick(() => bottom.value?.scrollIntoView({ behavior, block: 'end' }))
}

// ---------- Документы ----------
interface Document { id: string; name: string; created_at: string }
const documentsList = ref<{ reload: () => Promise<void> } | null>(null)
const uploading = ref(false)
const fileInput = ref<HTMLInputElement>()

function fetchDocumentsPage(offset: number, limit: number) {
  return get<PageOf<Document>>(`/ai/documents?limit=${limit}&offset=${offset}`)
}

async function downloadSource(documentId: string) {
  try {
    const response = await fetch(`/api/v1/ai/documents/${documentId}/download`, {
      headers: { Authorization: `Bearer ${auth.accessToken}` },
    })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'document.txt'
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (error) {
    toast.apiError(error)
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
    await documentsList.value?.reload()
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
    await documentsList.value?.reload()
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
const proposalsList = ref<{ reload: () => Promise<void> } | null>(null)
const autopapply = ref(false)
const deciding = ref<Record<string, boolean>>({})

function fetchProposalsPage(offset: number, limit: number) {
  return get<PageOf<Proposal>>(`/ai/proposals?limit=${limit}&offset=${offset}`)
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
    await proposalsList.value?.reload()
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

    <!-- Чат: обе колонки ограничены по высоте доступной области (100dvh −
         хром приложения ~15rem) — страница не растёт от контента ассистента -->
    <div v-if="tab === 'chat'" class="grid grid-cols-1 gap-4 lg:grid-cols-[240px_1fr]">
      <Card class="flex max-h-48 w-full flex-col overflow-hidden border-zinc-200 shadow-sm dark:border-zinc-800 lg:max-h-[calc(100dvh-15rem)] lg:w-60">
        <CardContent class="flex min-h-0 flex-col p-3">
          <Button v-if="canWrite" variant="outline" size="sm" class="mb-2 w-full shrink-0 gap-1.5" @click="newSession">
            <Plus class="h-3.5 w-3.5" /> {{ t('ai.newSession') }}
          </Button>
          <PaginatedList
            ref="sessionsList" :fetch-page="fetchSessionsPage"
            class="flex min-h-0 flex-1 flex-col" v-slot="{ items: sessionRows, loading: sessionsLoading }"
          >
            <div v-if="sessionsLoading" class="space-y-2">
              <Skeleton class="h-8 w-full" />
              <Skeleton class="h-8 w-full" />
            </div>
            <ul v-else class="erp-scroll min-h-0 flex-1 space-y-0.5 overflow-y-auto pr-0.5">
              <li
                v-for="session in sessionRows" :key="session.id"
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
              <li v-if="!sessionRows.length" class="px-2 py-1 text-xs text-muted-foreground">{{ t('ai.noSessions') }}</li>
            </ul>
          </PaginatedList>
        </CardContent>
      </Card>

      <Card class="flex h-[calc(100dvh-15rem)] max-h-[760px] min-h-[440px] flex-col border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="flex min-h-0 flex-1 flex-col p-0">
          <div class="erp-scroll min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
            <div
              v-for="(message, index) in messages" :key="index"
              class="flex flex-col" :class="message.role === 'user' && 'items-end'"
            >
              <div
                class="max-w-[75%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm leading-relaxed"
                :class="message.role === 'user'
                  ? 'bg-emerald-600 text-white'
                  : 'bg-zinc-100 dark:bg-zinc-800'"
              >{{ message.content }}</div>
              <div v-if="message.meta?.sources?.length" class="mt-1.5 max-w-[75%] space-y-1">
                <p class="text-[11px] font-medium text-muted-foreground">{{ t('ai.sources') }}:</p>
                <div
                  v-for="(source, sIndex) in message.meta.sources" :key="sIndex"
                  class="rounded-lg border border-zinc-200 bg-zinc-50/60 px-2.5 py-1.5 dark:border-zinc-800 dark:bg-zinc-900/60"
                >
                  <p class="line-clamp-2 text-[11px] leading-snug text-muted-foreground">«{{ source.text.slice(0, 220) }}{{ source.text.length > 220 ? '…' : '' }}»</p>
                  <div class="mt-1 flex items-center justify-between gap-2">
                    <span class="flex min-w-0 items-center gap-1 text-[11px] font-medium">
                      <FileText class="h-3 w-3 shrink-0 text-violet-500" />
                      <span class="truncate">{{ source.document_name }}</span>
                    </span>
                    <button
                      v-if="canWrite" type="button"
                      class="flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium text-emerald-700 transition-colors hover:bg-emerald-50 dark:text-emerald-400 dark:hover:bg-emerald-950/40"
                      :title="t('ai.downloadSource')"
                      @click="downloadSource(source.document_id)"
                    >
                      <Download class="h-3 w-3" /> {{ t('ai.download') }}
                    </button>
                  </div>
                </div>
              </div>
            </div>
            <div v-if="!messages.length && !sending && !polling" class="flex flex-1 items-center justify-center py-16">
              <div class="text-center">
                <span class="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-violet-500/10 text-violet-600 dark:text-violet-300">
                  <Bot class="h-6 w-6" />
                </span>
                <p class="mt-3 text-sm text-muted-foreground">{{ t('ai.placeholder') }}</p>
              </div>
            </div>
            <div v-if="sending || polling" class="max-w-[75%] self-start rounded-2xl bg-zinc-100 px-3.5 py-2 text-sm text-muted-foreground dark:bg-zinc-800">
              {{ sending ? t('ai.thinking') : t('ai.stillGenerating') }}<span class="animate-pulse">…</span>
            </div>
            <div ref="bottom" />
          </div>
          <form
            class="flex gap-2 border-t border-zinc-100 p-3 dark:border-zinc-800"
            @submit.prevent="sendMessage"
          >
            <Input
              v-model="input" :placeholder="t('ai.placeholder')" :disabled="sending || polling || !canWrite"
              class="flex-1"
            />
            <Button variant="emerald" size="icon" type="submit" :disabled="sending || polling || !input.trim() || !canWrite">
              <Send class="h-4 w-4" />
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>

    <!-- Документы -->
    <Card v-else-if="tab === 'documents'" class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-0">
        <PaginatedList
          ref="documentsList" :fetch-page="fetchDocumentsPage"
          v-slot="{ items: documentRows, loading: documentsLoading }"
        >
          <div class="flex items-center justify-between gap-3 flex-wrap px-4 py-3">
            <p class="text-sm font-semibold min-w-0 truncate">{{ t('ai.documents') }}</p>
            <div v-if="canWrite">
              <input ref="fileInput" type="file" class="hidden shrink-0" @change="uploadFile">
              <Button variant="emerald" size="sm" class="gap-1.5" :disabled="uploading" @click="pickFile">
                <Upload class="h-3.5 w-3.5" /> {{ uploading ? t('ai.uploading') : t('ai.upload') }}
              </Button>
            </div>
          </div>
          <div v-if="documentsLoading" class="space-y-2 p-4"><Skeleton class="h-10 w-full" /></div>
          <div v-else-if="documentRows.length === 0" class="p-6">
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
                <tr v-for="doc in documentRows" :key="doc.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
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
        </PaginatedList>
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
          <Button variant="ghost" size="sm" @click="proposalsList?.reload()">{{ t('sync.refresh') }}</Button>
        </CardContent>
      </Card>
      <PaginatedList
        ref="proposalsList" :fetch-page="fetchProposalsPage"
        v-slot="{ items: proposalRows, loading: proposalsLoading }"
      >
        <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
          <CardContent class="p-0">
            <div v-if="proposalsLoading" class="space-y-2 p-4"><Skeleton class="h-10 w-full" /></div>
            <div v-else-if="proposalRows.length === 0" class="p-6">
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
                  <tr v-for="row in proposalRows" :key="row.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
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
      </PaginatedList>
    </div>
  </div>
</template>

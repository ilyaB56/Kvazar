<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { ChatLineRound, Delete, Plus } from '@element-plus/icons-vue'
import { get, post, put, del } from '../api/client'

const { t } = useI18n()

interface Session { id: string; title: string; created_at: string }
interface Source { document_id: string; document_name: string; text: string }
interface Message { role: string; content: string; meta?: { sources?: Source[] } }

const sessions = ref<Session[]>([])
const activeId = ref<string | null>(null)
const messages = ref<Message[]>([])
const input = ref('')
const sending = ref(false)
const loadingSessions = ref(false)
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
  if (!text || sending.value) return
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
    ElMessage.error(error instanceof Error ? error.message : t('errors.unknown'))
  } finally {
    sending.value = false
    scrollToBottom()
  }
}

async function removeSession(id: string) {
  await del(`/ai/sessions/${id}`)
  if (activeId.value === id) {
    activeId.value = null
    messages.value = []
  }
  await loadSessions()
}

function newSession() {
  activeId.value = null
  messages.value = []
}

function scrollToBottom() {
  nextTick(() => bottom.value?.scrollIntoView({ behavior: 'smooth' }))
}

onMounted(loadSessions)

import { onMounted as _om } from 'vue'
// proposals tab state
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
  proposals.value = await get<Proposal[]>('/ai/proposals')
}

async function loadSettings() {
  const s = await get<{ autopapply: boolean }>('/ai/settings')
  autopapply.value = s.autopapply
}

async function toggleAutopapply(value: boolean | string | number) {
  const newValue = Boolean(value)
  await put('/ai/settings', { autopapply: newValue })
  autopapply.value = newValue
}

async function approve(row: Proposal) {
  deciding.value[row.id] = true
  try {
    await post(`/ai/proposals/${row.id}/approve`)
    await loadProposals()
  } finally {
    deciding.value[row.id] = false
  }
}

async function reject(row: Proposal) {
  deciding.value[row.id] = true
  try {
    await post(`/ai/proposals/${row.id}/reject`)
    await loadProposals()
  } finally {
    deciding.value[row.id] = false
  }
}

_om(() => { loadProposals(); loadSettings() })

</script>

<template>
  <div class="page assistant">
    <div class="page__header">
      <h2 class="page__title">{{ t('ai.title') }}</h2>
      <el-button :icon="Plus" @click="newSession">{{ t('ai.newSession') }}</el-button>
    </div>


    <el-tabs>
      <el-tab-pane :label="t('ai.tabChat')">
    <div class="assistant__layout">
      <el-aside width="240px" class="assistant__sessions">
        <div
          v-for="session in sessions"
          :key="session.id"
          class="assistant__session"
          :class="{ 'is-active': session.id === activeId }"
          @click="openSession(session.id)"
        >
          <span class="assistant__session-title">{{ session.title || t('ai.untitled') }}</span>
          <el-button :icon="Delete" link size="small" @click.stop="removeSession(session.id)" />
        </div>
        <div v-if="!sessions.length" class="muted">{{ t('ai.noSessions') }}</div>
      </el-aside>

      <div class="assistant__chat">
        <div class="assistant__messages">
          <div v-for="(message, index) in messages" :key="index"
               class="assistant__message" :class="`is-${message.role}`">
            <div class="assistant__bubble">{{ message.content }}</div>
            <div v-if="message.meta?.sources?.length" class="assistant__sources">
              <div class="muted">{{ t('ai.sources') }}:</div>
              <div v-for="(source, sIndex) in message.meta.sources" :key="sIndex" class="assistant__source">
                📄 {{ source.document_name }}
              </div>
            </div>
          </div>
          <div v-if="!messages.length" class="muted assistant__empty">
            {{ t('ai.placeholder') }}
          </div>
          <div ref="bottom" />
        </div>
        <div class="assistant__input">
          <el-input
            v-model="input"
            :placeholder="t('ai.placeholder')"
            :disabled="sending"
            @keyup.enter="sendMessage"
          />
          <el-button type="primary" :icon="ChatLineRound" :loading="sending" @click="sendMessage">
            {{ t('ai.send') }}
          </el-button>
        </div>
      </div>
    </div>
      </el-tab-pane>

      <el-tab-pane :label="t('ai.tabProposals')">
        <div class="assistant__autopapply">
          <el-switch v-model="autopapply" @change="toggleAutopapply" />
          <span>{{ t('ai.autopapply') }}</span>
          <span class="muted">{{ t('ai.autopapplyHint') }}</span>
        </div>
        <el-table :data="proposals">
          <el-table-column :label="t('ai.proposalType')" width="170">
            <template #default="{ row }">{{ t(`ai.action.${row.action_type}`) }}</template>
          </el-table-column>
          <el-table-column :label="t('ai.proposalPayload')" min-width="220">
            <template #default="{ row }">
              {{ row.payload.kind }} · {{ row.payload.amount }} {{ row.payload.currency }} · {{ row.payload.description }}
            </template>
          </el-table-column>
          <el-table-column prop="reason" :label="t('ai.proposalReason')" min-width="150" />
          <el-table-column :label="t('ai.proposalStatus')" width="130">
            <template #default="{ row }">
              <el-tag :type="row.status === 'approved' || row.status === 'auto_applied' ? 'success' : row.status === 'rejected' || row.status === 'failed' ? 'danger' : 'info'">
                {{ t(`ai.status.${row.status}`) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column width="200">
            <template #default="{ row }">
              <template v-if="row.status === 'pending'">
                <el-button size="small" type="success" :loading="deciding[row.id]" @click="approve(row)">
                  {{ t('ai.approve') }}
                </el-button>
                <el-button size="small" type="danger" :loading="deciding[row.id]" @click="reject(row)">
                  {{ t('ai.reject') }}
                </el-button>
              </template>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.assistant__layout {
  display: flex;
  gap: 16px;
  height: calc(100vh - 140px);
}

.assistant__sessions {
  border-right: 1px solid #e4e7ed;
  overflow-y: auto;
  padding-right: 8px;
}

.assistant__session {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px;
  border-radius: 6px;
  cursor: pointer;
}

.assistant__session:hover {
  background: #f5f7fa;
}

.assistant__session.is-active {
  background: #ecf5ff;
}

.assistant__session-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.assistant__chat {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.assistant__messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
}

.assistant__message {
  margin-bottom: 12px;
  display: flex;
  flex-direction: column;
}

.assistant__message.is-user {
  align-items: flex-end;
}

.assistant__bubble {
  max-width: 70%;
  padding: 8px 12px;
  border-radius: 10px;
  white-space: pre-wrap;
  background: #f5f7fa;
}

.is-user .assistant__bubble {
  background: #d9ecff;
}

.assistant__sources {
  margin-top: 4px;
  font-size: 12px;
}

.assistant__source {
  color: #909399;
}

.assistant__empty {
  text-align: center;
  margin-top: 40px;
}

.assistant__input {
  display: flex;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid #e4e7ed;
}

.assistant__autopapply {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}
</style>

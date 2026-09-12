<script setup lang="ts">
// Безопасность (этап G): смена пароля (инвалидирует все токены — security-p0),
// служебные API-токены (/admin/api-tokens, CRUD), политика паролей.
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { KeyRound, Monitor, Plus, ShieldCheck, Trash2 } from 'lucide-vue-next'
import { del, get, post } from '../api/client'
import {
  Badge, Button, Card, CardContent, Dialog, EmptyState, Input, Label,
  Skeleton, useToast,
} from '../components/ui'
import ChangePasswordDialog from '../layouts/ChangePasswordDialog.vue'
import { useAuthStore } from '../stores/auth'

const { t, d } = useI18n()
const auth = useAuthStore()
const toast = useToast()

const passwordOpen = ref(false)

// ---------- API-токены ----------
interface ApiToken {
  id: string
  name: string
  role: string
  is_active: boolean
  last_used_at: string | null
  created_at: string
}
const tokens = ref<ApiToken[] | null>(null)
const tokenSearch = ref('')
const filteredTokens = computed(() => {
  if (!tokens.value) return []
  const q = tokenSearch.value.trim().toLowerCase()
  return q ? tokens.value.filter((t2) => t2.name.toLowerCase().includes(q)) : tokens.value
})
const tokenOpen = ref(false)
const tokenCreating = ref(false)
const tokenForm = reactive({ name: '', role: 'readonly' })
// секрет показывается один раз при создании
const freshSecret = ref<string | null>(null)

async function loadTokens() {
  try {
    tokens.value = await get<ApiToken[]>('/admin/api-tokens')
  } catch {
    tokens.value = null // не админ или раздел недоступен
  }
}
onMounted(loadTokens)

async function createToken() {
  if (tokenCreating.value || !tokenForm.name.trim()) return
  tokenCreating.value = true
  try {
    const created = await post<{ token: string }>('/admin/api-tokens', {
      name: tokenForm.name.trim(), role: tokenForm.role,
    })
    freshSecret.value = created.token
    tokenForm.name = ''
    await loadTokens()
  } catch (error) {
    toast.apiError(error)
  } finally {
    tokenCreating.value = false
  }
}

async function revokeToken(row: ApiToken) {
  try {
    await del(`/admin/api-tokens/${row.id}`)
    toast.success(t('security.tokenRevoked'))
    await loadTokens()
  } catch (error) {
    toast.apiError(error)
  }
}

// ---------- Активные сеансы (sessions-security §2.3) ----------
interface AuthSession {
  id: string
  user_agent: string
  ip: string
  created_at: string
  last_used_at: string | null
  is_current: boolean
}
const sessions = ref<AuthSession[] | null>(null)
const sessionBusy = ref<Record<string, boolean>>({})

async function loadSessions() {
  try {
    sessions.value = await get<AuthSession[]>('/auth/sessions')
  } catch {
    sessions.value = null
  }
}
onMounted(loadSessions)

// подтверждение паролем (спека §2.2/2.3): Завершить / Завершить все другие
const confirmOpen = ref(false)
const confirmMode = ref<'one' | 'others'>('others')
const confirmTarget = ref<AuthSession | null>(null)
const confirmPassword = ref('')
const confirmError = ref('')
const confirmBusy = ref(false)

function askConfirm(mode: 'one' | 'others', row: AuthSession | null = null) {
  confirmMode.value = mode
  confirmTarget.value = row
  confirmPassword.value = ''
  confirmError.value = ''
  confirmOpen.value = true
}

async function doConfirmRevoke() {
  if (confirmBusy.value || !confirmPassword.value) return
  confirmBusy.value = true
  confirmError.value = ''
  try {
    if (confirmMode.value === 'others') {
      const terminated = await auth.logoutOthers(confirmPassword.value)
      toast.success(t('sessions.terminatedToast', { n: terminated }))
      auth.activeSessions = 1
    } else if (confirmTarget.value) {
      await post(`/auth/sessions/${confirmTarget.value.id}/revoke`, {
        password: confirmPassword.value,
      })
      toast.success(t('sessions.revokedToast'))
    }
    confirmOpen.value = false
    await loadSessions()
  } catch (error) {
    const { status, retryAfter } = error as { status?: number; retryAfter?: number }
    if (status === 403) confirmError.value = t('sessions.wrongPassword')
    else if (status === 429) {
      confirmError.value = t('sessions.rateLimited', { n: retryAfter ?? 60 })
    } else {
      confirmError.value = (error as Error).message
    }
  } finally {
    confirmBusy.value = false
  }
}

/** Браузер/ОС из user_agent — для показа самому пользователю. */
function deviceLabel(ua: string): string {
  if (!ua) return t('sessions.unknownDevice')
  const browsers: Array<[RegExp, string]> = [
    [/YaBrowser\/([\d.]+)/, 'Yandex Browser'],
    [/Edg\/([\d.]+)/, 'Edge'],
    [/OPR\/([\d.]+)/, 'Opera'],
    [/Chrome\/([\d.]+)/, 'Chrome'],
    [/Firefox\/([\d.]+)/, 'Firefox'],
    [/Safari\/([\d.]+)/, 'Safari'],
    [/python-requests|httpx|sessions-security-test/, 'API-клиент'],
  ]
  const systems: Array<[RegExp, string]> = [
    [/Windows NT 10/, 'Windows'],
    [/Windows/, 'Windows'],
    [/Mac OS X/, 'macOS'],
    [/Android/, 'Android'],
    [/iPhone|iPad/, 'iOS'],
    [/Linux/, 'Linux'],
  ]
  const browser = browsers.find(([re]) => re.test(ua))?.[1] ?? ''
  const system = systems.find(([re]) => re.test(ua))?.[1] ?? ''
  return [system, browser].filter(Boolean).join(' · ') || t('sessions.unknownDevice')
}

async function copySecret(value: string) {
  try {
    await navigator.clipboard.writeText(value)
    toast.success(t('connections.copied'))
  } catch {
    toast.error(t('connections.copyFailed'))
  }
}
</script>

<template>
  <div class="space-y-4">
    <!-- Пароль и сессии -->
    <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="flex flex-wrap items-center justify-between gap-3 p-5">
        <div class="flex items-start gap-3">
          <span class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600 dark:bg-emerald-950/60 dark:text-emerald-400">
            <KeyRound class="h-4 w-4" />
          </span>
          <div>
            <p class="text-sm font-semibold">{{ t('security.passwordTitle') }}</p>
            <p class="mt-0.5 max-w-lg text-xs leading-relaxed text-muted-foreground">
              {{ t('security.passwordHint') }}
            </p>
          </div>
        </div>
        <Button variant="outline" size="sm" @click="passwordOpen = true">{{ t('password.submit') }}</Button>
      </CardContent>
    </Card>

    <!-- Активные сеансы (sessions-security) -->
    <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="flex items-center gap-2 text-sm font-semibold">
            <Monitor class="h-4 w-4 text-emerald-600" /> {{ t('sessions.title') }}
          </p>
          <Button
            v-if="sessions && sessions.length > 1" variant="outline" size="sm"
            @click="askConfirm('others')"
          >
            {{ t('sessions.revokeOthers') }}
          </Button>
        </div>
        <p class="mt-1 text-xs text-muted-foreground">{{ t('sessions.hint') }}</p>

        <div v-if="sessions && sessions.length === 0" class="py-8">
          <EmptyState :title="t('ui.emptyTitle')" :description="t('sessions.empty')" />
        </div>
        <div v-else-if="sessions" class="mt-3 space-y-2">
          <div
            v-for="row in sessions" :key="row.id"
            class="flex flex-wrap items-center gap-3 rounded-lg border border-zinc-200 px-3 py-2.5 dark:border-zinc-800"
          >
            <span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
              <Monitor class="h-4 w-4" />
            </span>
            <div class="min-w-0 flex-1">
              <p class="flex items-center gap-2 text-sm font-medium">
                {{ deviceLabel(row.user_agent) }}
                <Badge v-if="row.is_current" variant="secondary" class="text-[10px]">
                  {{ t('sessions.current') }}
                </Badge>
              </p>
              <p class="mt-0.5 text-xs text-muted-foreground">
                IP {{ row.ip || '—' }} · {{ t('sessions.entered') }} {{ d(row.created_at, 'short') }} ·
                {{ t('sessions.activity') }} {{ row.last_used_at ? d(row.last_used_at, 'short') : '—' }}
              </p>
            </div>
            <Button
              v-if="!row.is_current" variant="ghost" size="sm" class="text-red-600 hover:text-red-700 dark:text-red-400"
              @click="askConfirm('one', row)"
            >
              <Trash2 class="mr-1.5 h-3.5 w-3.5" /> {{ t('sessions.revoke') }}
            </Button>
          </div>
        </div>
        <Skeleton v-else class="mt-3 h-20 w-full" />
      </CardContent>
    </Card>

    <!-- API-токены (админ) -->
    <Card v-if="auth.isAdmin && tokens" class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="flex items-center gap-2 text-sm font-semibold">
            <ShieldCheck class="h-4 w-4 text-emerald-600" /> {{ t('security.tokensTitle') }}
          </p>
          <Input v-if="tokens.length" v-model="tokenSearch" :placeholder="t('ui.searchPlaceholder')" class="h-8 w-[200px]" />
          <Button variant="outline" size="sm" class="gap-1.5" @click="tokenOpen = true">
            <Plus class="h-3.5 w-3.5" /> {{ t('security.newToken') }}
          </Button>
        </div>
        <p class="mt-1 text-xs text-muted-foreground">{{ t('security.tokensHint') }}</p>

        <div v-if="tokens.length === 0" class="py-8">
          <EmptyState :title="t('ui.emptyTitle')" :description="t('security.noTokens')" />
        </div>
        <div v-else class="mt-3 overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                <th class="px-3 py-2 font-medium">{{ t('security.tokenName') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('security.tokenRole') }}</th>
                <th class="hidden px-3 py-2 font-medium sm:table-cell">{{ t('security.tokenUsed') }}</th>
                <th class="px-3 py-2 font-medium" />
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in tokens" :key="row.id" class="border-t border-zinc-100 dark:border-zinc-800/70">
                <td class="px-3 py-2 font-medium">
                  {{ row.name }}
                  <Badge v-if="!row.is_active" variant="secondary" class="ml-1.5 text-[10px]">
                    {{ t('security.revoked') }}
                  </Badge>
                </td>
                <td class="px-3 py-2 text-muted-foreground">{{ t(`roles.${row.role}`) ?? row.role }}</td>
                <td class="hidden px-3 py-2 text-muted-foreground sm:table-cell">
                  {{ row.last_used_at ? d(row.last_used_at, 'short') : '—' }}
                </td>
                <td class="px-3 py-2 text-right">
                  <Button
                    v-if="row.is_active" variant="ghost" size="icon" class="h-7 w-7"
                    :title="t('security.revoke')" @click="revokeToken(row)"
                  >
                    <Trash2 class="h-3.5 w-3.5 text-red-500" />
                  </Button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
    <Skeleton v-else-if="auth.isAdmin && tokens === null" class="h-40 w-full" />

    <ChangePasswordDialog v-model="passwordOpen" />

    <!-- Подтверждение паролем разрушительных действий над сеансами -->
    <Dialog :open="confirmOpen" :title="t('sessions.confirmTitle')" width="440px"
            @update:open="(v: boolean) => { if (!v) confirmOpen = false }">
      <form class="space-y-4" @submit.prevent="doConfirmRevoke">
        <p class="text-sm leading-relaxed text-muted-foreground">
          {{ confirmMode === 'others'
            ? t('sessions.confirmOthersText')
            : t('sessions.confirmOneText', { device: confirmTarget ? deviceLabel(confirmTarget.user_agent) : '' }) }}
        </p>
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('sessions.passwordLabel') }}</Label>
          <Input v-model="confirmPassword" type="password" placeholder="••••••••" />
        </div>
        <p v-if="confirmError" class="text-xs font-medium text-red-600 dark:text-red-400">
          {{ confirmError }}
        </p>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" type="button" @click="confirmOpen = false">
            {{ t('ui.cancel') }}
          </Button>
          <Button variant="emerald" size="sm" type="submit"
                  :disabled="confirmBusy || !confirmPassword">
            {{ t('sessions.confirmButton') }}
          </Button>
        </div>
      </form>
    </Dialog>

    <!-- Диалог нового токена -->
    <Dialog :open="tokenOpen" :title="t('security.newToken')" @update:open="(v: boolean) => { if (!v) { tokenOpen = false; freshSecret = null } }">
      <div v-if="!freshSecret" class="space-y-4">
        <form class="space-y-4" @submit.prevent="createToken">
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('security.tokenName') }}</Label>
            <Input v-model="tokenForm.name" placeholder="1C-обмен" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('security.tokenRole') }}</Label>
            <div class="w-[200px]">
              <select
                v-model="tokenForm.role"
                class="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm shadow-sm"
              >
                <option value="readonly">{{ t('roles.readonly') }}</option>
                <option value="user">{{ t('roles.user') }}</option>
              </select>
            </div>
          </div>
          <div class="flex justify-end gap-2">
            <Button variant="outline" size="sm" @click="tokenOpen = false">{{ t('ui.cancel') }}</Button>
            <Button variant="emerald" type="submit" size="sm" :disabled="tokenCreating || !tokenForm.name.trim()">
              {{ t('ui.save') }}
            </Button>
          </div>
        </form>
      </div>
      <div v-else class="space-y-3">
        <p class="text-sm text-muted-foreground">{{ t('security.secretOnce') }}</p>
        <div class="flex items-center gap-2 rounded-lg bg-zinc-100 p-2 dark:bg-zinc-800">
          <code class="flex-1 break-all text-xs">{{ freshSecret }}</code>
          <Button variant="outline" size="sm" @click="copySecret(freshSecret)">{{ t('security.copy') }}</Button>
        </div>
        <div class="flex justify-end">
          <Button variant="outline" size="sm" @click="tokenOpen = false; freshSecret = null">
            {{ t('security.done') }}
          </Button>
        </div>
      </div>
    </Dialog>
  </div>
</template>

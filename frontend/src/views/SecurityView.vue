<script setup lang="ts">
// Безопасность (этап G): смена пароля (инвалидирует все токены — security-p0),
// служебные API-токены (/admin/api-tokens, CRUD), политика паролей.
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { KeyRound, Plus, ShieldCheck, Trash2 } from 'lucide-vue-next'
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

    <!-- API-токены (админ) -->
    <Card v-if="auth.isAdmin && tokens" class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="flex items-center gap-2 text-sm font-semibold">
            <ShieldCheck class="h-4 w-4 text-emerald-600" /> {{ t('security.tokensTitle') }}
          </p>
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

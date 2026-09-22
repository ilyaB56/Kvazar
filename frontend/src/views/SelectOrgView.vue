<script setup lang="ts">
// Выбор организации (multitenancy-spec §9): после входа супер-админа
// платформы — список организаций + «Открыть»; экран сам и есть платформенный
// режим: реестр с созданием организации (админ + временный пароль один раз)
// и переключением активности. Обычный пользователь сюда не попадает (guard).
// Реестр пагинирован (по 50 + infinite scroll) — организаций сотни, полный
// список подвешивал экран.
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { Building2, Hourglass, KeyRound, Plus, Power, PowerOff, UserPlus, X } from 'lucide-vue-next'
import { get, patch, post } from '../api/client'
import {
  Badge, Button, Card, CardContent, Dialog, EmptyState, Input, Label,
  PaginatedList, Skeleton, TempPasswordDialog, useToast,
} from '../components/ui'
import type { PageOf } from '../components/ui'
import { useAuthStore } from '../stores/auth'

// ---------- Заявки на подключение (self-service signup) ----------
interface SignupRequest {
  id: string
  company_name: string
  contact_name: string
  email: string
  status: 'pending' | 'verified'
  created_at: string
  verified_at: string | null
}

const requests = ref<SignupRequest[]>([])
const requestsLoading = ref(false)
const requestBusy = ref<string | null>(null)

async function loadRequests() {
  requestsLoading.value = true
  try {
    requests.value = await get<SignupRequest[]>('/platform/signup-requests')
  } catch (error) {
    toast.apiError(error)
  } finally {
    requestsLoading.value = false
  }
}

onMounted(loadRequests)

async function approveRequest(req: SignupRequest) {
  if (requestBusy.value) return
  requestBusy.value = req.id
  try {
    const org = await post<{ name: string; admin: { email: string } }>(
      `/platform/signup-requests/${req.id}/approve`)
    toast.success(t('mt.orgCreatedToast', { name: org.name })
      + t('mt.orgCreatedAdminToast', { email: org.admin.email }))
    await Promise.all([list.value?.reload(), loadRequests()])
  } catch (error) {
    toast.apiError(error)
  } finally {
    requestBusy.value = null
  }
}

async function rejectRequest(req: SignupRequest) {
  if (requestBusy.value) return
  requestBusy.value = req.id
  try {
    await post(`/platform/signup-requests/${req.id}/reject`)
    await loadRequests()
  } catch (error) {
    toast.apiError(error)
  } finally {
    requestBusy.value = null
  }
}

interface Org {
  id: string
  name: string
  inn: string
  is_active: boolean
  users_count: number
  admin_id: string | null
  admin_email: string | null
}

const { t } = useI18n()
const router = useRouter()
const auth = useAuthStore()
const toast = useToast()

const list = ref<{ reload: () => Promise<void> } | null>(null)

function fetchOrgPage(offset: number, limit: number) {
  return get<PageOf<Org>>(`/platform/orgs?limit=${limit}&offset=${offset}`)
}

const opening = ref<string | null>(null)

// поиск по организациям: клиентская фильтрация загруженных строк
const orgSearch = ref('')
function filterOrgs(orgs: Org[]): Org[] {
  const q = orgSearch.value.trim().toLowerCase()
  if (!q) return orgs
  return orgs.filter((o) => o.name.toLowerCase().includes(q)
    || (o.inn ?? '').toLowerCase().includes(q))
}

// сброс пароля администратора организации (только платформенный админ)
const resetOpen = ref(false)
const resetTarget = ref<Org | null>(null)
const resetPassword = ref<string | null>(null)
const resetWorking = ref(false)

async function resetAdminPassword(org: Org) {
  if (resetWorking.value || !org.admin_id) return
  resetWorking.value = true
  try {
    const response = await post<{ temp_password: string }>(
      `/platform/users/${org.admin_id}/reset-password`)
    resetPassword.value = response.temp_password
    resetTarget.value = org
    resetOpen.value = true
  } catch (error) {
    toast.apiError(error)
  } finally {
    resetWorking.value = false
  }
}

async function openOrg(org: Org) {
  if (opening.value || !org.is_active) return
  opening.value = org.id
  try {
    await auth.selectOrg(org.id)
    await auth.fetchMe()
    await auth.fetchPermissions()
    router.push('/dashboard')
  } catch (error) {
    toast.apiError(error)
  } finally {
    opening.value = null
  }
}

async function toggleOrg(org: Org) {
  try {
    await patch(`/platform/orgs/${org.id}`, { is_active: !org.is_active })
    await list.value?.reload()
  } catch (error) {
    toast.apiError(error)
  }
}

// ---------- Создание организации (админ + временный пароль один раз) ----------
const createOpen = ref(false)
const creating = ref(false)
const form = reactive({ name: '', inn: '', admin_email: '', admin_full_name: '' })
const created = ref<{
  name: string; temp_password: string; admin_email: string
} | null>(null)

async function createOrg() {
  if (creating.value || !form.name.trim() || !form.admin_email.trim()) return
  creating.value = true
  try {
    const response = await post<{
      name: string; temp_password: string; admin_email: string
    }>('/platform/orgs', {
      name: form.name.trim(), inn: form.inn.trim(),
      admin_email: form.admin_email.trim(), admin_full_name: form.admin_full_name.trim(),
    })
    created.value = response
    await list.value?.reload()
  } catch (error) {
    toast.apiError(error)
  } finally {
    creating.value = false
  }
}

function closeCreate() {
  createOpen.value = false
  created.value = null
  form.name = ''
  form.inn = ''
  form.admin_email = ''
  form.admin_full_name = ''
}
</script>

<template>
  <div class="mx-auto max-w-3xl space-y-4">
    <div class="flex items-center justify-between gap-3">
      <div>
        <h2 class="text-xl font-semibold">{{ t('mt.selectOrgTitle') }}</h2>
        <p class="mt-1 text-sm text-muted-foreground">{{ t('mt.selectOrgHint') }}</p>
      </div>
      <Button variant="emerald" size="sm" class="gap-1.5" @click="createOpen = true">
        <Plus class="h-3.5 w-3.5" /> {{ t('mt.createOrg') }}
      </Button>
    </div>

    <!-- Заявки на подключение (self-service signup) -->
    <Card v-if="requests.length" class="border-amber-200 shadow-sm dark:border-amber-900/60">
      <CardContent class="space-y-2 p-4">
        <p class="flex items-center gap-2 text-sm font-semibold">
          <UserPlus class="h-4 w-4 text-amber-500" /> {{ t('mt.pendingTitle') }}
        </p>
        <div
          v-for="req in requests" :key="req.id"
          class="flex flex-wrap items-center gap-3 rounded-lg border border-zinc-200 p-3 dark:border-zinc-800"
        >
          <span class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-amber-50 text-amber-600 dark:bg-amber-950/40 dark:text-amber-400">
            <Hourglass class="h-4 w-4" />
          </span>
          <div class="min-w-0 flex-1">
            <p class="truncate text-sm font-medium">{{ req.company_name }}</p>
            <p class="mt-0.5 truncate text-xs text-muted-foreground">
              {{ req.contact_name }} · {{ req.email }} ·
              {{ new Date(req.created_at).toLocaleDateString() }} ·
              <span :class="req.status === 'verified'
                ? 'text-emerald-600 dark:text-emerald-400'
                : 'text-amber-600 dark:text-amber-400'">
                {{ req.status === 'verified' ? t('mt.statusVerified') : t('mt.statusPending') }}
              </span>
            </p>
          </div>
          <Button
            v-if="req.status === 'verified'" variant="emerald" size="sm"
            :disabled="requestBusy === req.id" @click="approveRequest(req)"
          >
            {{ t('mt.approve') }}
          </Button>
          <Button
            variant="ghost" size="sm" :title="t('mt.reject')"
            :disabled="requestBusy === req.id" @click="rejectRequest(req)"
          >
            <X class="h-4 w-4 text-red-500" />
          </Button>
        </div>
      </CardContent>
    </Card>

    <PaginatedList ref="list" :fetch-page="fetchOrgPage" v-slot="{ items: orgs, loading }">
      <div class="flex flex-wrap items-center gap-2 pb-1">
        <Input
          v-model="orgSearch" :placeholder="t('mt.searchOrgs')"
          class="h-9 max-w-xs" :disabled="loading"
        />
        <span v-if="orgSearch.trim() && !loading" class="text-xs text-muted-foreground">
          {{ t('mt.foundOf', { n: filterOrgs(orgs).length, m: orgs.length }) }}
        </span>
      </div>
      <Skeleton v-if="loading" class="h-40 w-full" />
      <Card v-else-if="!orgs.length" class="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="py-10">
          <EmptyState :title="t('ui.emptyTitle')" :description="t('mt.noOrgs')" />
        </CardContent>
      </Card>
      <div v-else class="space-y-2">
        <Card
          v-for="org in filterOrgs(orgs)" :key="org.id"
          class="border-zinc-200 shadow-sm transition-colors dark:border-zinc-800"
          :class="!org.is_active && 'opacity-60'"
        >
          <CardContent class="flex flex-wrap items-center gap-3 p-4">
            <span class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600 dark:bg-emerald-950/60 dark:text-emerald-400">
              <Building2 class="h-5 w-5" />
            </span>
            <div class="min-w-0 flex-1">
              <p class="flex items-center gap-2 text-sm font-semibold">
                {{ org.name }}
                <Badge v-if="!org.is_active" variant="secondary" class="text-[10px]">
                  {{ t('mt.deactivated') }}
                </Badge>
              </p>
              <p class="mt-0.5 text-xs text-muted-foreground">
                {{ t('mt.usersCount', { n: org.users_count }) }}<span v-if="org.inn"> · ИНН {{ org.inn }}</span>
              </p>
            </div>
            <Button
              v-if="org.is_active" variant="emerald" size="sm"
              :disabled="opening === org.id" @click="openOrg(org)"
            >
              {{ opening === org.id ? t('mt.opening') : t('mt.open') }}
            </Button>
            <Button
              v-if="org.admin_id" variant="ghost" size="sm"
              :title="t('mt.resetAdmin')" :disabled="resetWorking"
              @click="resetAdminPassword(org)"
            >
              <KeyRound class="h-4 w-4 text-sky-600" />
            </Button>
            <Button
              variant="ghost" size="sm"
              :title="org.is_active ? t('mt.deactivate') : t('mt.activate')"
              @click="toggleOrg(org)"
            >
              <PowerOff v-if="org.is_active" class="h-4 w-4 text-amber-500" />
              <Power v-else class="h-4 w-4 text-emerald-500" />
            </Button>
          </CardContent>
        </Card>
      </div>
    </PaginatedList>

    <!-- Создание организации -->
    <Dialog :open="createOpen" :title="created ? t('mt.orgCreatedTitle') : t('mt.createOrg')"
            width="520px" @update:open="(v: boolean) => { if (!v) closeCreate() }">
      <div v-if="!created" class="space-y-4">
        <form class="space-y-3" @submit.prevent="createOrg">
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('mt.orgName') }}</Label>
            <Input v-model="form.name" placeholder="ООО «Ромашка»" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">ИНН</Label>
            <Input v-model="form.inn" placeholder="7707083893" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('mt.adminEmail') }}</Label>
            <Input v-model="form.admin_email" placeholder="director@romashka.ru" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('mt.adminName') }}</Label>
            <Input v-model="form.admin_full_name" placeholder="Иван Иванов" />
          </div>
          <div class="flex justify-end gap-2">
            <Button variant="outline" size="sm" type="button" @click="closeCreate">
              {{ t('ui.cancel') }}
            </Button>
            <Button variant="emerald" size="sm" type="submit"
                    :disabled="creating || !form.name.trim() || !form.admin_email.trim()">
              {{ creating ? t('mt.creating') : t('mt.create') }}
            </Button>
          </div>
        </form>
      </div>
      <div v-else class="space-y-4">
        <p class="text-sm text-muted-foreground">{{ t('mt.orgCreatedText', { name: created.name }) }}</p>
        <div class="space-y-2 rounded-lg bg-zinc-100 p-3 dark:bg-zinc-800">
          <p class="text-xs text-muted-foreground">{{ t('mt.adminEmail') }}: <span class="font-medium">{{ created.admin_email }}</span></p>
          <p class="text-xs font-medium text-red-600 dark:text-red-400">{{ t('mt.tempPasswordOnce') }}</p>
          <code class="block break-all rounded bg-background px-2 py-1.5 text-sm">{{ created.temp_password }}</code>
        </div>
        <div class="flex justify-end">
          <Button variant="outline" size="sm" @click="closeCreate">{{ t('mt.done') }}</Button>
        </div>
      </div>
    </Dialog>

    <!-- Сброс пароля администратора организации -->
    <TempPasswordDialog
      :open="resetOpen" :title="t('mt.resetAdminTitle')"
      :hint="t('mt.resetAdminHint', { name: resetTarget?.name ?? '' })"
      :temp-password="resetPassword"
      @update:open="(v: boolean) => { resetOpen = v }"
    />
  </div>
</template>

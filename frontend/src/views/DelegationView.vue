<script setup lang="ts">
// Пользователи и делегирование (role-delegation §7/§12, этап C): список
// своей организации, личные гранты чипами, выдача/отозвать (выдающий видит
// только свои rw-модули), создание «пустой» учётки с автогенерацией логина
// из ФИО (предпросмотр), первая выдача = активация временным паролем.
// Список пользователей пагинирован (PaginatedList, по 50 + infinite
// scroll); поиск — локальный по загруженным строкам.
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { KeyRound, Plus, ShieldOff, UserPlus } from 'lucide-vue-next'
import { del, get, post, put } from '../api/client'
import {
  Badge, Button, Card, CardContent, Dialog, EmptyState, Input, Label,
  PaginatedList, SearchSelect, Skeleton, TempPasswordDialog, useToast,
} from '../components/ui'
import type { PageOf } from '../components/ui'
import { useAuthStore } from '../stores/auth'

const { t, d } = useI18n()
const auth = useAuthStore()
const toast = useToast()

interface UserRow {
  id: string
  email: string
  full_name: string
  role: string
  is_active: boolean
}
interface GrantRow {
  user_id: string
  email: string
  full_name: string
  module: string
  level: string
  granted_at: string
}

const usersList = ref<{ reload: () => Promise<void> } | null>(null)
// total из @loaded — показывать ли поиск (есть ли вообще пользователи)
const usersTotal = ref(0)
const delegations = ref<{ grantable: Record<string, string>; grants: GrantRow[] } | null>(null)
const delegationsLoaded = ref(false)
const search = ref('')
const busy = ref<Record<string, boolean>>({})

function fetchUsersPage(offset: number, limit: number) {
  return get<PageOf<UserRow>>(`/users?limit=${limit}&offset=${offset}`)
}

// сброс пароля сотрудника (§7.4): сотрудник своей org, не себя
const resetOpen = ref(false)
const resetTarget = ref<UserRow | null>(null)
const resetPassword = ref<string | null>(null)
const resetWorking = ref(false)

async function resetUserPassword(row: UserRow) {
  if (resetWorking.value) return
  resetWorking.value = true
  try {
    const response = await post<{ temp_password: string }>(
      `/users/${row.id}/reset-password`)
    resetPassword.value = response.temp_password
    resetTarget.value = row
    resetOpen.value = true
  } catch (error) {
    toast.apiError(error)
  } finally {
    resetWorking.value = false
  }
}

function filterUsers(rows: UserRow[]): UserRow[] {
  const q = search.value.trim().toLowerCase()
  return q
    ? rows.filter(u =>
        (u.email ?? '').toLowerCase().includes(q)
        || (u.full_name ?? '').toLowerCase().includes(q))
    : rows
}

const canDelegate = computed(() =>
  auth.isAdmin || Object.keys(delegations.value?.grantable ?? {}).length > 0)

const grantableModules = computed(() =>
  Object.keys(delegations.value?.grantable ?? {}).map(m => ({
    value: m,
    label: t(`modules.${m}`),
  })))

async function loadDelegations() {
  try {
    delegations.value = await get<{ grantable: Record<string, string>; grants: GrantRow[] }>('/delegations')
  } catch (error) {
    toast.apiError(error)
    delegations.value = { grantable: {}, grants: [] }
  } finally {
    delegationsLoaded.value = true
  }
}
onMounted(loadDelegations)

const grantsByUser = computed(() => {
  const map: Record<string, GrantRow[]> = {}
  for (const g of delegations.value?.grants ?? []) {
    map[g.user_id] = [...(map[g.user_id] ?? []), g]
  }
  return map
})

// ---------- Выдача / отзыв ----------
const grantOpen = ref(false)
const grantForm = reactive({ user: null as UserRow | null, module: 'accounting', level: 'ro' })
const grantResult = ref<{ temp_password: string; must_change_password_by: string } | null>(null)

function askGrant(user: UserRow) {
  grantForm.user = user
  grantForm.module = grantableModules.value[0]?.value ?? 'accounting'
  grantForm.level = 'ro'
  grantResult.value = null
  grantOpen.value = true
}

async function doGrant() {
  if (!grantForm.user || busy.value.grant) return
  busy.value.grant = true
  try {
    const result = await put<{ temp_password?: string; must_change_password_by?: string }>(
      `/users/${grantForm.user.id}/permissions`,
      { module: grantForm.module, level: grantForm.level })
    if (result.temp_password) {
      // §12.3: первая выдача = активация — пароль показан один раз
      grantResult.value = {
        temp_password: result.temp_password,
        must_change_password_by: result.must_change_password_by ?? '',
      }
    } else {
      toast.success(t('deleg.grantedToast'))
      grantOpen.value = false
    }
    await usersList.value?.reload()
    await loadDelegations()
  } catch (error) {
    toast.apiError(error)
  } finally {
    busy.value.grant = false
  }
}

async function revoke(userId: string, module: string) {
  busy.value[`${userId}:${module}`] = true
  try {
    await del(`/users/${userId}/permissions/${module}`)
    toast.success(t('deleg.revokedToast'))
    await usersList.value?.reload()
    await loadDelegations()
  } catch (error) {
    toast.apiError(error)
  } finally {
    busy.value[`${userId}:${module}`] = false
  }
}

// ---------- Создание учётки (§12.1) ----------
const createOpen = ref(false)
const creating = ref(false)
const createForm = reactive({ full_name: '', email: '', phone: '' })
const createdUser = ref<{ id: string; username: string } | null>(null)

// Предпросмотр логина: инициалы+фамилия транслитом (совпадает с бэкендом)
const TRANSLIT: Record<string, string> = {
  а: 'A', б: 'B', в: 'V', г: 'G', д: 'D', е: 'E', ё: 'E', ж: 'ZH', з: 'Z',
  и: 'I', й: 'I', к: 'K', л: 'L', м: 'M', н: 'N', о: 'O', п: 'P', р: 'R',
  с: 'S', т: 'T', у: 'U', ф: 'F', х: 'KH', ц: 'TS', ч: 'CH', ш: 'SH',
  щ: 'SHCH', ъ: '', ы: 'Y', ь: '', э: 'E', ю: 'IU', я: 'IA',
}
const usernamePreview = computed(() => {
  const parts = createForm.full_name.trim().split(/\s+/).filter(Boolean)
  if (parts.length < 2) return ''
  const translit = (s: string) =>
    [...s.toLowerCase()].map(ch => TRANSLIT[ch] ?? (/[a-z0-9]/.test(ch) ? ch.toUpperCase() : '')).join('')
  const surname = translit(parts[parts.length - 1])
  const initials = parts.slice(0, -1).map(translit).map(s => s[0]).join('')
  return (initials + surname).slice(0, 64) || ''
})

async function doCreate() {
  if (creating.value || !createForm.full_name.trim()) return
  creating.value = true
  try {
    const result = await post<{ id: string; username: string }>('/accounts', {
      full_name: createForm.full_name.trim(),
      email: createForm.email.trim(),
      phone: createForm.phone.trim(),
    })
    createdUser.value = result
    toast.success(t('deleg.accountCreated'))
    await usersList.value?.reload()
    await loadDelegations()
  } catch (error) {
    toast.apiError(error)
  } finally {
    creating.value = false
  }
}

function closeCreate() {
  createOpen.value = false
  createdUser.value = null
  createForm.full_name = ''
  createForm.email = ''
  createForm.phone = ''
}
</script>

<template>
  <div class="space-y-4">
    <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="flex flex-wrap items-center justify-between gap-3 p-5">
        <div class="flex items-center gap-3">
          <span class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-violet-50 text-violet-600 dark:bg-violet-950/60 dark:text-violet-400">
            <KeyRound class="h-4 w-4" />
          </span>
          <div>
            <p class="text-sm font-semibold">{{ t('deleg.title') }}</p>
            <p class="mt-0.5 max-w-xl text-xs leading-relaxed text-muted-foreground">
              {{ t('deleg.hint') }}
            </p>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <Input v-if="usersTotal" v-model="search" :placeholder="t('ui.searchPlaceholder')" class="h-8 w-[200px]" />
          <Button v-if="canDelegate" variant="emerald" size="sm" class="gap-1.5" @click="createOpen = true">
            <UserPlus class="h-3.5 w-3.5" /> {{ t('deleg.newAccount') }}
          </Button>
        </div>
      </CardContent>
    </Card>

    <div v-if="!canDelegate && delegationsLoaded" class="py-8">
      <EmptyState :title="t('ui.emptyTitle')" :description="t('deleg.noGrantable')" />
    </div>

    <PaginatedList
      v-if="canDelegate" ref="usersList" :fetch-page="fetchUsersPage"
      @loaded="(total: number) => { usersTotal = total }"
      v-slot="{ items: userRows, loading }"
    >
      <Skeleton v-if="loading" class="h-40 w-full" />
      <Card v-else-if="filterUsers(userRows).length" class="border-zinc-200 shadow-sm dark:border-zinc-800">
        <CardContent class="p-0">
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="bg-zinc-50/80 text-left text-xs text-muted-foreground dark:bg-zinc-900/50">
                  <th class="px-3 py-2 font-medium">{{ t('deleg.colUser') }}</th>
                  <th class="px-3 py-2 font-medium">{{ t('deleg.colRole') }}</th>
                  <th class="px-3 py-2 font-medium">{{ t('deleg.colGrants') }}</th>
                  <th class="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in filterUsers(userRows)" :key="row.id"
                    class="border-t border-zinc-100 dark:border-zinc-800/70">
                  <td class="px-3 py-2">
                    <p class="font-medium">{{ row.full_name || '—' }}</p>
                    <p class="text-xs text-muted-foreground">{{ row.email }}</p>
                  </td>
                  <td class="px-3 py-2">
                    <Badge :class="row.role === 'admin'
                      ? 'bg-violet-100 text-violet-800 dark:bg-violet-950/60 dark:text-violet-300'
                      : 'bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300'">
                      {{ t(`roles.${row.role}`) }}
                    </Badge>
                  </td>
                  <td class="px-3 py-2">
                    <div class="flex flex-wrap gap-1">
                      <span
                        v-for="g in grantsByUser[row.id] ?? []" :key="g.module"
                        class="group inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300"
                      >
                        {{ t(`modules.${g.module}`) }}·{{ g.level }}
                        <button
                          v-if="auth.isAdmin || g.module" type="button"
                          class="opacity-0 transition-opacity group-hover:opacity-100"
                          :title="t('deleg.revoke')"
                          @click="revoke(row.id, g.module)"
                        >×</button>
                      </span>
                      <span v-if="!(grantsByUser[row.id] ?? []).length" class="text-xs text-muted-foreground">—</span>
                    </div>
                  </td>
                  <td class="px-3 py-2 text-right">
                    <div class="flex justify-end gap-1">
                      <Button
                        v-if="grantableModules.length && row.is_active" variant="outline" size="sm"
                        class="gap-1" @click="askGrant(row)"
                      >
                        <Plus class="h-3.5 w-3.5" /> {{ t('deleg.grant') }}
                      </Button>
                      <Button
                        v-if="row.id !== auth.user?.id" variant="ghost" size="icon" class="h-8 w-8"
                        :title="t('deleg.resetPassword')" :disabled="resetWorking"
                        @click="resetUserPassword(row)"
                      >
                        <KeyRound class="h-3.5 w-3.5 text-sky-600" />
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

    <!-- Диалог выдачи -->
    <Dialog :open="grantOpen" :title="t('deleg.grantTitle')" width="440px"
            @update:open="(v: boolean) => { if (!v) grantOpen = false }">
      <div v-if="!grantResult" class="space-y-4">
        <p class="text-sm text-muted-foreground">
          {{ grantForm.user?.full_name || grantForm.user?.email }}
        </p>
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('deleg.module') }}</Label>
          <SearchSelect
            v-model="grantForm.module"
            :options="grantableModules"
            :placeholder="t('deleg.modulePlaceholder')"
          />
        </div>
        <div class="space-y-1.5">
          <Label class="text-xs font-medium">{{ t('deleg.level') }}</Label>
          <div class="flex gap-2">
            <Button :variant="grantForm.level === 'ro' ? 'emerald' : 'outline'" size="sm"
                    type="button" @click="grantForm.level = 'ro'">ro — {{ t('deleg.readonly') }}</Button>
            <Button :variant="grantForm.level === 'rw' ? 'emerald' : 'outline'" size="sm"
                    type="button" @click="grantForm.level = 'rw'">rw — {{ t('deleg.readwrite') }}</Button>
          </div>
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="grantOpen = false">{{ t('ui.cancel') }}</Button>
          <Button variant="emerald" size="sm" :disabled="busy.grant" @click="doGrant">
            {{ t('deleg.grant') }}
          </Button>
        </div>
      </div>
      <div v-else class="space-y-4">
        <p class="text-sm text-muted-foreground">{{ t('deleg.activatedHint') }}</p>
        <p class="text-xs font-medium text-red-600 dark:text-red-400">{{ t('mfa.saveNowWarning') }}</p>
        <code class="block break-all rounded-lg bg-zinc-100 px-3 py-2 text-sm dark:bg-zinc-800">
          {{ grantResult.temp_password }}
        </code>
        <p class="text-xs text-muted-foreground">
          {{ t('deleg.deadline') }}: {{ d(grantResult.must_change_password_by, 'short') }}
        </p>
        <div class="flex justify-end">
          <Button variant="emerald" size="sm" @click="grantOpen = false">{{ t('mfa.savedDone') }}</Button>
        </div>
      </div>
    </Dialog>

    <!-- Создание учётки -->
    <Dialog :open="createOpen" :title="t('deleg.newAccount')" width="520px"
            @update:open="(v: boolean) => { if (!v) closeCreate() }">
      <div v-if="!createdUser" class="space-y-4">
        <form class="space-y-3" @submit.prevent="doCreate">
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('deleg.fullName') }}</Label>
            <Input v-model="createForm.full_name" placeholder="Иван Иванович Иванов" />
            <p v-if="usernamePreview" class="text-xs text-muted-foreground">
              {{ t('deleg.loginPreview') }}: <code class="font-semibold">{{ usernamePreview }}</code>
            </p>
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">Email</Label>
            <Input v-model="createForm.email" type="email" placeholder="ivanov@company.ru" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('deleg.phone') }}</Label>
            <Input v-model="createForm.phone" placeholder="+7 900 000-00-00" />
          </div>
          <div class="flex justify-end gap-2">
            <Button variant="outline" size="sm" type="button" @click="closeCreate">{{ t('ui.cancel') }}</Button>
            <Button variant="emerald" size="sm" type="submit" :disabled="creating || !createForm.full_name.trim()">
              {{ creating ? t('mfa.loading') : t('ui.save') }}
            </Button>
          </div>
        </form>
      </div>
      <div v-else class="space-y-4">
        <p class="text-sm text-muted-foreground">{{ t('deleg.accountCreatedHint') }}</p>
        <div class="rounded-lg bg-zinc-100 p-3 dark:bg-zinc-800">
          <p class="text-xs text-muted-foreground">{{ t('deleg.login') }}:</p>
          <code class="text-lg font-semibold tracking-wide">{{ createdUser.username }}</code>
        </div>
        <p class="text-xs text-muted-foreground">{{ t('deleg.emptyAccountHint') }}</p>
        <div class="flex justify-end">
          <Button variant="emerald" size="sm" @click="closeCreate">{{ t('mfa.savedDone') }}</Button>
        </div>
      </div>
    </Dialog>
    <!-- Сброс пароля сотрудника -->
    <TempPasswordDialog
      :open="resetOpen" :title="t('deleg.resetTitle')"
      :hint="t('deleg.resetHint', { name: resetTarget?.full_name || resetTarget?.email || '' })"
      :temp-password="resetPassword"
      @update:open="(v: boolean) => { resetOpen = v }"
    />
  </div>
</template>

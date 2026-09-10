<script setup lang="ts">
// Доступы и роли (редизайн §6.4): карточки ролей + матрица роли × модули,
// клик по ячейке циклит rw → ro → none; admin — неизменяемая (замок).
// «Применить права» — PUT всей матрицы каждой изменённой роли; «Новая роль» —
// POST с slug-ключом от бэкенда. Каркас SystemView — временно, перенос в G.
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { CheckCircle2, KeyRound, Lock, Plus } from 'lucide-vue-next'
import { get, post, put } from '../api/client'
import {
  Button, Card, CardContent, CardDescription, CardHeader, CardTitle,
  Dialog, Input, Label, Skeleton, useToast,
} from '../components/ui'
import { cn } from '../lib/utils'

const { t } = useI18n()
const toast = useToast()

interface Role {
  key: string
  name: string
  description: string
  is_builtin: boolean
  color: string
  users_count: number
  permissions: Record<string, string>
}

const MODULES = ['accounting', 'crm', 'integrations', 'ai', 'system'] as const
type PermValue = 'rw' | 'ro' | 'none'

const roles = ref<Role[] | null>(null)
// черновик матрицы: role_key -> module -> уровень (edit-local, PUT по кнопке)
const draft = reactive<Record<string, Record<string, string>>>({})
const applying = ref(false)

const dirty = computed(() => {
  if (!roles.value) return false
  return roles.value.some((role) =>
    MODULES.some((m) => (draft[role.key]?.[m] ?? 'none') !== role.permissions[m]))
})

async function load() {
  roles.value = null
  try {
    const data = await get<Role[]>('/roles')
    roles.value = data
    for (const role of data) {
      draft[role.key] = { ...role.permissions }
    }
  } catch {
    roles.value = []
    toast.error(t('errors.unknown'))
  }
}
onMounted(load)

function cycle(roleKey: string, module: string) {
  if (roleKey === 'admin') return
  const current = draft[roleKey]?.[module] ?? 'none'
  const next: PermValue = current === 'rw' ? 'ro' : current === 'ro' ? 'none' : 'rw'
  draft[roleKey][module] = next
}

async function apply() {
  if (!roles.value || applying.value) return
  applying.value = true
  try {
    for (const role of roles.value) {
      if (MODULES.every((m) => (draft[role.key]?.[m] ?? 'none') === role.permissions[m])) continue
      await put(`/roles/${role.key}/permissions`, { permissions: draft[role.key] })
    }
    toast.success(t('permissions.applied'))
    await load()
  } catch {
    toast.error(t('errors.unknown'))
  } finally {
    applying.value = false
  }
}

// ---------- Новая роль ----------

const createOpen = ref(false)
const creating = ref(false)
const newRole = reactive({ name: '', description: '' })

async function createRole() {
  if (creating.value) return
  if (newRole.name.trim().length < 2) return
  creating.value = true
  try {
    await post('/roles', { name: newRole.name.trim(), description: newRole.description.trim() })
    toast.success(t('permissions.roleCreated'))
    createOpen.value = false
    newRole.name = ''
    newRole.description = ''
    await load()
  } catch {
    toast.error(t('errors.unknown'))
  } finally {
    creating.value = false
  }
}

const colorMap: Record<string, string> = {
  emerald: 'bg-emerald-600', teal: 'bg-teal-600', amber: 'bg-amber-600',
  orange: 'bg-orange-600', zinc: 'bg-zinc-600', violet: 'bg-violet-600',
  sky: 'bg-sky-600', red: 'bg-red-600',
}

// русская морфология «N сотрудников» — явные правила, как в эталоне
function employeesLabel(n: number): string {
  if (n === 0) return t('permissions.employeesNone')
  const mod10 = n % 10
  const mod100 = n % 100
  const one = mod10 === 1 && mod100 !== 11
  const few = mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)
  return one ? t('permissions.employeesOne', { n })
    : few ? t('permissions.employeesFew', { n })
      : t('permissions.employeesMany', { n })
}
const valueMeta: Record<string, { short: string; cls: string }> = {
  rw: { short: 'R/W', cls: 'text-emerald-600 dark:text-emerald-400' },
  ro: { short: 'R/O', cls: 'text-amber-600 dark:text-amber-400' },
  none: { short: '—', cls: 'text-zinc-400 dark:text-zinc-600' },
}
</script>

<template>
  <Card class="border-zinc-200 shadow-sm dark:border-zinc-800">
    <CardHeader class="flex-row items-center justify-between space-y-0 pb-4">
      <div>
        <CardTitle class="flex items-center gap-2 text-base">
          <KeyRound class="h-4 w-4 text-emerald-600" /> {{ t('permissions.title') }}
        </CardTitle>
        <CardDescription>{{ t('permissions.hint') }}</CardDescription>
      </div>
      <div class="flex items-center gap-2">
        <Button variant="outline" size="sm" class="gap-1.5 text-xs" @click="createOpen = true">
          <Plus class="h-3.5 w-3.5" /> {{ t('permissions.newRole') }}
        </Button>
        <Button
          size="sm" :variant="dirty ? 'emerald' : 'outline'" :disabled="!dirty || applying"
          class="gap-1.5 text-xs" @click="apply"
        >
          <CheckCircle2 class="h-3.5 w-3.5" />
          {{ dirty ? t('permissions.apply') : t('permissions.noChanges') }}
        </Button>
      </div>
    </CardHeader>
    <CardContent class="space-y-4">
      <!-- Скелет загрузки -->
      <div v-if="roles === null" class="space-y-2">
        <Skeleton class="h-16 w-full" />
        <Skeleton class="h-16 w-full" />
      </div>
      <template v-else>
        <!-- Карточки ролей -->
        <div class="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          <div
            v-for="role in roles" :key="role.key"
            class="rounded-lg border border-zinc-200 bg-zinc-50/60 p-2.5 transition-colors hover:border-emerald-300 dark:border-zinc-800 dark:bg-zinc-900/50 dark:hover:border-emerald-800"
          >
            <div class="flex items-center gap-2">
              <span :class="cn('h-2.5 w-2.5 shrink-0 rounded-sm', colorMap[role.color] ?? 'bg-zinc-600')" />
              <p class="truncate text-xs font-semibold">{{ role.name }}</p>
            </div>
            <p class="mt-1 line-clamp-2 text-[10px] leading-snug text-muted-foreground">{{ role.description }}</p>
            <p class="mt-1.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-400">
              {{ employeesLabel(role.users_count) }}
            </p>
          </div>
        </div>

        <!-- Матрица прав -->
        <div class="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-zinc-50/80 dark:bg-zinc-900/50">
                <th class="min-w-[160px] px-3 py-2 text-left font-medium">{{ t('permissions.role') }}</th>
                <th
                  v-for="m in MODULES" :key="m"
                  class="px-2 py-2 text-center text-xs font-medium"
                >{{ t(`permissions.modules.${m}`) }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="role in roles" :key="role.key"
                class="border-t border-zinc-100 dark:border-zinc-800/70"
              >
                <td class="px-3 py-2">
                  <span class="flex items-center gap-2 text-[13px] font-medium">
                    <span :class="cn('h-2 w-2 shrink-0 rounded-full', colorMap[role.color] ?? 'bg-zinc-600')" />
                    {{ role.name }}
                    <Lock
                      v-if="role.key === 'admin'"
                      class="h-3 w-3 text-muted-foreground"
                      :aria-label="t('permissions.legendAdmin')"
                    />
                  </span>
                </td>
                <td v-for="m in MODULES" :key="m" class="px-2 py-2 text-center">
                  <button
                    type="button"
                    :disabled="role.key === 'admin' || applying"
                    :title="t(`permissions.values.${draft[role.key]?.[m] ?? 'none'}`)"
                    :aria-label="`${role.name} · ${t(`permissions.modules.${m}`)}: ${t(`permissions.values.${draft[role.key]?.[m] ?? 'none'}`)}`"
                    :class="cn(
                      'inline-flex h-7 min-w-[52px] items-center justify-center rounded-md border text-[10px] font-bold transition-all',
                      role.key === 'admin'
                        ? 'cursor-default border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900'
                        : 'border-zinc-200 hover:border-emerald-400 hover:bg-emerald-50 active:scale-95 dark:border-zinc-700 dark:hover:border-emerald-700 dark:hover:bg-emerald-950/40',
                      valueMeta[draft[role.key]?.[m] ?? 'none'].cls,
                    )"
                    @click="cycle(role.key, m)"
                  >
                    {{ valueMeta[draft[role.key]?.[m] ?? 'none'].short }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Легенда -->
        <div class="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-muted-foreground">
          <span class="font-medium">{{ t('permissions.legend') }}</span>
          <span class="flex items-center gap-1.5"><span class="font-bold text-emerald-600 dark:text-emerald-400">R/W</span> {{ t('permissions.legendRw') }}</span>
          <span class="flex items-center gap-1.5"><span class="font-bold text-amber-600 dark:text-amber-400">R/O</span> {{ t('permissions.legendRo') }}</span>
          <span class="flex items-center gap-1.5"><span class="font-bold text-zinc-400 dark:text-zinc-600">—</span> {{ t('permissions.legendNone') }}</span>
          <span class="ml-auto flex items-center gap-1"><Lock class="h-3 w-3" /> {{ t('permissions.legendAdmin') }}</span>
        </div>
      </template>
    </CardContent>
  </Card>

  <Dialog v-model:open="createOpen" :title="t('permissions.dialogTitle')">
    <form class="space-y-4" @submit.prevent="createRole">
      <div class="space-y-1.5">
        <Label for="role-name" class="text-xs font-medium">{{ t('permissions.name') }}</Label>
        <Input id="role-name" v-model="newRole.name" :placeholder="t('permissions.namePlaceholder')" />
      </div>
      <div class="space-y-1.5">
        <Label for="role-description" class="text-xs font-medium">{{ t('permissions.description') }}</Label>
        <Input id="role-description" v-model="newRole.description" :placeholder="t('permissions.descriptionPlaceholder')" />
      </div>
      <div class="flex justify-end gap-2">
        <Button variant="outline" size="sm" @click="createOpen = false">{{ t('ui.cancel') }}</Button>
        <Button variant="emerald" type="submit" size="sm" :disabled="creating || newRole.name.trim().length < 2">
          {{ creating ? t('permissions.creating') : t('permissions.create') }}
        </Button>
      </div>
    </form>
  </Dialog>
</template>

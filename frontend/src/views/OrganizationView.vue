<script setup lang="ts">
// Организация (этап G): карточка компании из /companies + редактирование
// name/ИНН (админ). Компания одна (v1) — первая из списка.
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Building2 } from 'lucide-vue-next'
import { get, patch } from '../api/client'
import { Button, Card, CardContent, Input, Label, Skeleton, useToast } from '../components/ui'
import { useAuthStore } from '../stores/auth'

const { t } = useI18n()
const auth = useAuthStore()
const toast = useToast()

interface Company {
  id: string
  name: string
  inn: string | null
}

const company = ref<Company | null>(null)
const loaded = ref(false)
const editing = ref(false)
const saving = ref(false)
const form = reactive({ name: '', inn: '' })

async function load() {
  const list = await get<Company[]>('/companies')
  company.value = list[0] ?? null
  loaded.value = true
}

function startEdit() {
  form.name = company.value?.name ?? ''
  form.inn = company.value?.inn ?? ''
  editing.value = true
}

async function save() {
  if (saving.value || !company.value || form.name.trim().length < 2) return
  saving.value = true
  try {
    await patch(`/companies/${company.value.id}`, { name: form.name.trim(), inn: form.inn.trim() })
    toast.success(t('org.saved'))
    editing.value = false
    await load()
  } catch (error) {
    toast.apiError(error)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-4">
    <Skeleton v-if="!loaded" class="h-40 w-full" />
    <Card v-if="company" class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-6">
        <div class="flex items-start justify-between gap-4">
          <div class="flex items-center gap-3">
            <span class="flex h-11 w-11 items-center justify-center rounded-xl bg-emerald-50 text-emerald-600 dark:bg-emerald-950/60 dark:text-emerald-400">
              <Building2 class="h-5 w-5" />
            </span>
            <div>
              <p class="text-base font-bold">{{ company.name }}</p>
              <p class="text-xs text-muted-foreground">ИНН: {{ company.inn || '—' }}</p>
            </div>
          </div>
          <Button v-if="auth.isAdmin && !editing" variant="outline" size="sm" @click="startEdit">
            {{ t('org.edit') }}
          </Button>
        </div>

        <form v-if="editing" class="mt-5 max-w-md space-y-4" @submit.prevent="save">
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">{{ t('org.name') }}</Label>
            <Input v-model="form.name" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-medium">ИНН</Label>
            <Input v-model="form.inn" placeholder="7707083893" />
          </div>
          <div class="flex gap-2">
            <Button variant="outline" size="sm" @click="editing = false">{{ t('permissions.cancel') }}</Button>
            <Button variant="emerald" type="submit" size="sm" :disabled="saving || form.name.trim().length < 2">
              {{ t('permissions.apply') }}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
    <Card v-else-if="loaded && !company" class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-6 text-sm text-muted-foreground">{{ t('org.empty') }}</CardContent>
    </Card>
  </div>
</template>

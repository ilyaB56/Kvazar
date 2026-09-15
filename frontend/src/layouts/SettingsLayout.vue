<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { Tabs } from '../components/ui'
import { useAuthStore } from '../stores/auth'

// Финальный дом настроек (этап G, §7-11): организация, профиль, безопасность,
// доступы и роли (матрица §6.4 — переехала окончательно), система
// (бэкапы/обновления/журнал/параметры контура).
const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const tabs = computed(() => [
  { key: 'organization', label: t('settings.organization') },
  { key: 'profile', label: t('settings.profile') },
  { key: 'security', label: t('settings.security') },
  ...(auth.isAdmin || Object.values(auth.permissions ?? {}).some(l => l === 'rw') ? [
    { key: 'delegation', label: t('settings.delegation') },
  ] : []),
  ...(auth.isAdmin ? [
    { key: 'permissions', label: t('settings.permissions') },
    { key: 'system', label: t('settings.system') },
  ] : []),
])
const active = computed(() => {
  const match = route.path.match(/\/settings\/(\w+)/)
  return match?.[1] ?? 'organization'
})
</script>

<template>
  <div class="space-y-4">
    <Tabs :tabs="tabs" :model-value="active" @update:model-value="router.push(`/settings/${$event}`)" />
    <router-view />
  </div>
</template>

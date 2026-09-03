<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { Tabs } from '../components/ui'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const tabs = computed(() => [
  { key: 'connections', label: t('integrations.connections') },
  { key: 'sync', label: t('integrations.sync') },
  { key: 'notifications', label: t('integrations.notifications') },
])
const active = computed(() => {
  const match = route.path.match(/\/integrations\/(\w+)/)
  return match?.[1] ?? 'connections'
})
</script>

<template>
  <div class="space-y-4">
    <Tabs :tabs="tabs" :model-value="active" @update:model-value="router.push(`/integrations/${$event}`)" />
    <router-view />
  </div>
</template>

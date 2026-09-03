<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { Tabs } from '../components/ui'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const tabs = computed(() => [
  { key: 'system', label: t('settings.system') },
])
const active = computed(() => {
  const match = route.path.match(/\/settings\/(\w+)/)
  return match?.[1] ?? 'system'
})
</script>

<template>
  <div class="space-y-4">
    <Tabs :tabs="tabs" :model-value="active" @update:model-value="router.push(`/settings/${$event}`)" />
    <router-view />
  </div>
</template>

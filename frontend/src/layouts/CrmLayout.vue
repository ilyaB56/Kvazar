<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { Tabs } from '../components/ui'
import { useAuthStore } from '../stores/auth'

// Продажи (этап I): сделки CRM + документы продаж — вкладки одного раздела.
const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const tabs = computed(() => [
  ...(auth.moduleLevel('crm') !== 'none'
    ? [{ key: 'deals', label: t('crm.tabDeals') }]
    : []),
  ...(auth.moduleLevel('accounting') !== 'none'
    ? [{ key: 'orders', label: t('crm.tabOrders') }]
    : []),
])
const active = computed(() => {
  const match = route.path.match(/\/crm\/(\w+)/)
  return match?.[1] ?? 'deals'
})
</script>

<template>
  <div class="space-y-4">
    <Tabs v-if="tabs.length > 1" :tabs="tabs" :model-value="active" @update:model-value="router.push(`/crm/${$event}`)" />
    <router-view />
  </div>
</template>

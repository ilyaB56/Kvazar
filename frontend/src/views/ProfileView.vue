<script setup lang="ts">
// Профиль (этап G): карточка пользователя /auth/me + смена пароля
// (ChangePasswordDialog — общая для профиля и меню оболочки).
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { KeyRound } from 'lucide-vue-next'
import { get } from '../api/client'
import type { User } from '../api/types'
import { Avatar, Button, Card, CardContent, Skeleton } from '../components/ui'
import ChangePasswordDialog from '../layouts/ChangePasswordDialog.vue'

const { t } = useI18n()

const user = ref<User | null>(null)
const passwordOpen = ref(false)

onMounted(async () => {
  user.value = await get<User>('/auth/me')
})

function initials(): string {
  const name = user.value?.full_name || user.value?.email || ''
  return name.slice(0, 2).toUpperCase()
}
</script>

<template>
  <div class="space-y-4">
    <Skeleton v-if="!user" class="h-44 w-full" />
    <Card v-if="user" class="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardContent class="p-6">
        <div class="flex flex-wrap items-center justify-between gap-4">
          <div class="flex items-center gap-4">
            <Avatar :initials="initials()" size="lg" />
            <div>
              <p class="text-base font-bold">{{ user.full_name || user.email }}</p>
              <p class="text-xs text-muted-foreground">{{ user.email }}</p>
              <p class="mt-1 text-xs text-emerald-700 dark:text-emerald-400">{{ t(`roles.${user.role}`) }}</p>
            </div>
          </div>
          <Button variant="outline" size="sm" class="gap-1.5" @click="passwordOpen = true">
            <KeyRound class="h-3.5 w-3.5" /> {{ t('password.submit') }}
          </Button>
        </div>
        <p class="mt-4 text-xs leading-relaxed text-muted-foreground">{{ t('profile.passwordHint') }}</p>
      </CardContent>
    </Card>
    <ChangePasswordDialog v-model="passwordOpen" />
  </div>
</template>

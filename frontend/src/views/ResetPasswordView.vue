<script setup lang="ts">
// Восстановление пароля по email-ссылке (multitenancy §7.5, этап D):
// /reset-password?token=… из письма (1 час, одноразовый).
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { KeyRound, MailCheck } from 'lucide-vue-next'
import { post } from '../api/client'
import { Button, Card, CardContent, Input, Label, useToast } from '../components/ui'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const toast = useToast()

const token = ref(typeof route.query.token === 'string' ? route.query.token : '')
const password = ref('')
const repeat = ref('')
const busy = ref(false)
const done = ref(false)
const error = ref('')

onMounted(() => {
  if (!token.value) error.value = t('recovery.noToken')
})

async function submit() {
  if (busy.value || done.value) return
  if (password.value.length < 8) {
    error.value = t('recovery.tooShort')
    return
  }
  if (password.value !== repeat.value) {
    error.value = t('recovery.mismatch')
    return
  }
  busy.value = true
  error.value = ''
  try {
    await post('/auth/reset-password', {
      token: token.value, new_password: password.value,
    })
    done.value = true
    toast.success(t('recovery.doneToast'))
    setTimeout(() => router.push('/login'), 2500)
  } catch (e) {
    const status = (e as { status?: number }).status
    if (status === 410) error.value = t('recovery.invalidToken')
    else if (status === 422) error.value = t('recovery.weakPassword')
    else error.value = t('errors.unknown')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-background p-4">
    <Card class="w-full max-w-[420px] border-zinc-200 shadow-xl dark:border-zinc-800">
      <CardContent class="p-6 sm:p-8">
        <template v-if="!done">
          <h2 class="text-xl font-bold tracking-tight">{{ t('recovery.title') }}</h2>
          <p class="mt-1 text-sm text-muted-foreground">{{ t('recovery.subtitle') }}</p>
          <form class="mt-5 space-y-4" @submit.prevent="submit">
            <div class="space-y-1.5">
              <Label class="text-xs font-medium">{{ t('recovery.newPassword') }}</Label>
              <Input v-model="password" type="password" autocomplete="new-password" />
            </div>
            <div class="space-y-1.5">
              <Label class="text-xs font-medium">{{ t('recovery.repeat') }}</Label>
              <Input v-model="repeat" type="password" autocomplete="new-password" />
            </div>
            <p v-if="error" class="text-xs font-medium text-red-600 dark:text-red-400">{{ error }}</p>
            <Button type="submit" variant="emerald" class="w-full gap-1.5"
                    :disabled="busy || !token || !password || !repeat">
              <KeyRound class="h-4 w-4" /> {{ t('recovery.submit') }}
            </Button>
          </form>
        </template>
        <div v-else class="flex flex-col items-center gap-3 py-6">
          <span class="flex h-12 w-12 items-center justify-center rounded-full bg-emerald-100 text-emerald-600 dark:bg-emerald-950/60">
            <MailCheck class="h-6 w-6" />
          </span>
          <p class="text-sm font-medium">{{ t('recovery.doneTitle') }}</p>
          <p class="text-xs text-muted-foreground">{{ t('recovery.doneHint') }}</p>
        </div>
      </CardContent>
    </Card>
  </div>
</template>

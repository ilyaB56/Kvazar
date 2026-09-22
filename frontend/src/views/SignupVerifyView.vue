<script setup lang="ts">
// Подтверждение email заявки на подключение: /signup/verify?token=…
// (ссылка из письма, 24 часа, одноразовая). Верифицируем автоматически.
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { AlertCircle, CheckCircle2, Loader2 } from 'lucide-vue-next'
import { post } from '../api/client'
import { Card, CardContent, Skeleton } from '../components/ui'

const { t } = useI18n()
const route = useRoute()

const state = ref<'loading' | 'ok' | 'error'>('loading')

onMounted(async () => {
  const token = typeof route.query.token === 'string' ? route.query.token : ''
  if (!token) {
    state.value = 'error'
    return
  }
  try {
    await post('/auth/signup/verify', { token })
    state.value = 'ok'
  } catch {
    state.value = 'error'
  }
})
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-background p-4">
    <Card class="w-full max-w-[420px] border-zinc-200 shadow-xl dark:border-zinc-800">
      <CardContent class="p-6 sm:p-8">
        <div v-if="state === 'loading'" class="flex flex-col items-center gap-3 py-6">
          <Loader2 class="h-8 w-8 animate-spin text-muted-foreground" />
          <Skeleton class="h-4 w-40" />
          <p class="text-xs text-muted-foreground">{{ t('signupVerify.checking') }}</p>
        </div>
        <div v-else-if="state === 'ok'" class="flex flex-col items-center gap-3 py-6">
          <span class="flex h-12 w-12 items-center justify-center rounded-full bg-emerald-100 text-emerald-600 dark:bg-emerald-950/60">
            <CheckCircle2 class="h-6 w-6" />
          </span>
          <p class="text-sm font-medium">{{ t('signupVerify.doneTitle') }}</p>
          <p class="text-xs leading-relaxed text-muted-foreground">{{ t('signupVerify.doneHint') }}</p>
        </div>
        <div v-else class="flex flex-col items-center gap-3 py-6">
          <span class="flex h-12 w-12 items-center justify-center rounded-full bg-red-100 text-red-600 dark:bg-red-950/60">
            <AlertCircle class="h-6 w-6" />
          </span>
          <p class="text-sm font-medium">{{ t('signupVerify.errorTitle') }}</p>
          <p class="text-xs leading-relaxed text-muted-foreground">{{ t('signupVerify.errorHint') }}</p>
          <router-link
            to="/signup"
            class="mt-2 text-sm font-medium text-emerald-600 hover:underline dark:text-emerald-400"
          >{{ t('signupVerify.retry') }}</router-link>
        </div>
      </CardContent>
    </Card>
  </div>
</template>

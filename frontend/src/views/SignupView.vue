<script setup lang="ts">
// Публичная заявка на подключение организации (self-service): форма →
// POST /auth/signup → письмо с подтверждением (24 ч). Детали ответа не
// раскрываются (всегда ok). Скелет — как у SignupVerifyView (рабочая
// страница той же связки Card/Label/Input).
import { reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { CheckCircle2, Send } from 'lucide-vue-next'
import { post } from '../api/client'
import {
  Button, Card, CardContent, Input, Label, useToast,
} from '../components/ui'
import QuasarMark from '../components/brand/QuasarMark.vue'

const { t } = useI18n()
const toast = useToast()

const form = reactive({
  company_name: '',
  name: '',
  email: '',
  password: '',
})
const busy = ref(false)
const sent = ref(false)

const valid = () =>
  form.company_name.trim().length >= 2 && form.name.trim() !== ''
  && form.email.trim() !== '' && form.password.length >= 8

async function submit() {
  if (busy.value || sent.value || !valid()) return
  busy.value = true
  try {
    await post('/auth/signup', {
      company_name: form.company_name.trim(),
      name: form.name.trim(),
      email: form.email.trim(),
      password: form.password,
    })
    sent.value = true
  } catch (error) {
    const status = (error as { status?: number }).status
    if (status === 422) {
      toast.error(t('signup.weakPassword'))
    } else {
      toast.apiError(error)
    }
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="flex min-h-screen flex-col items-center justify-center bg-background p-4">
    <div class="mb-6 flex items-center gap-2.5">
      <span class="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-xl shadow-lg shadow-violet-500/25 ring-1 ring-zinc-200 dark:ring-zinc-700">
        <QuasarMark class="h-8 w-8" />
      </span>
      <div class="leading-tight">
        <p class="text-base font-bold tracking-tight">{{ t('brand.name') }}</p>
        <p class="text-xs text-muted-foreground">{{ t('brand.tagline') }}</p>
      </div>
    </div>

    <Card class="w-full max-w-[420px] border-zinc-200 shadow-xl dark:border-zinc-800">
      <CardContent class="p-6 sm:p-8">
        <div v-if="!sent">
          <h2 class="text-xl font-bold tracking-tight">{{ t('signup.title') }}</h2>
          <p class="mt-1 text-sm text-muted-foreground">{{ t('signup.subtitle') }}</p>
          <form class="mt-5 space-y-4" @submit.prevent="submit">
            <div class="space-y-1.5">
              <Label class="text-xs font-medium">{{ t('signup.company') }}</Label>
              <Input v-model="form.company_name" autocomplete="organization"
                     :placeholder="t('signup.companyPlaceholder')" />
            </div>
            <div class="space-y-1.5">
              <Label class="text-xs font-medium">{{ t('signup.name') }}</Label>
              <Input v-model="form.name" autocomplete="name"
                     :placeholder="t('signup.namePlaceholder')" />
            </div>
            <div class="space-y-1.5">
              <Label class="text-xs font-medium">{{ t('signup.email') }}</Label>
              <Input v-model="form.email" type="email" autocomplete="email"
                     :placeholder="t('signup.emailPlaceholder')" />
            </div>
            <div class="space-y-1.5">
              <Label class="text-xs font-medium">{{ t('signup.password') }}</Label>
              <Input v-model="form.password" type="password" autocomplete="new-password"
                     :placeholder="t('signup.passwordPlaceholder')" />
              <p class="text-xs text-muted-foreground">{{ t('signup.passwordHint') }}</p>
            </div>
            <Button type="submit" variant="emerald" class="w-full gap-1.5" :disabled="busy || !valid()">
              <Send class="h-4 w-4" /> {{ busy ? t('signup.submitting') : t('signup.submit') }}
            </Button>
          </form>
          <p class="mt-5 text-center text-xs text-muted-foreground">
            {{ t('signup.haveAccount') }}
            <router-link to="/login" class="font-medium text-emerald-600 hover:underline dark:text-emerald-400">
              {{ t('signup.toLogin') }}
            </router-link>
          </p>
        </div>
        <div v-else class="flex flex-col items-center gap-3 py-6">
          <span class="flex h-12 w-12 items-center justify-center rounded-full bg-emerald-100 text-emerald-600 dark:bg-emerald-950/60">
            <CheckCircle2 class="h-6 w-6" />
          </span>
          <p class="text-sm font-medium">{{ t('signup.doneTitle') }}</p>
          <p class="text-xs leading-relaxed text-muted-foreground">{{ t('signup.doneHint') }}</p>
          <router-link to="/login" class="mt-2 text-sm font-medium text-emerald-600 hover:underline dark:text-emerald-400">
            {{ t('signup.toLogin') }}
          </router-link>
        </div>
      </CardContent>
    </Card>
  </div>
</template>

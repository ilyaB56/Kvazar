<script setup lang="ts">
// Мастер 2FA (multitenancy §2.3, этап C): руководитель (admin/pl) без
// включённой 2FA после входа проходит настройку — QR (локальный рендер
// otpauth-URI, без CDN — дух ADR-001) → код подтверждения → резервные
// коды с предупреждением «сохраните сейчас». Показ один раз за вход,
// не блокирует интерфейс (можно закрыть — напоминание вернётся).
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import QRCode from 'qrcode'
import { KeyRound, ShieldCheck } from 'lucide-vue-next'
import { Button, Dialog, Input, Label, useToast } from '../components/ui'
import { useAuthStore } from '../stores/auth'

const { t } = useI18n()
const auth = useAuthStore()
const toast = useToast()

const open = ref(false)
const step = ref<'qr' | 'code' | 'codes'>('qr')
const secret = ref('')
const otpauthUri = ref('')
const qrDataUrl = ref('')
const code = ref('')
const error = ref('')
const busy = ref(false)
const backupCodes = ref<string[]>([])

// «Настроить позже» скрывается после первого нажатия — до следующего входа
// (localStorage: один отказ за сессию браузера, возврат — новый вход)
const DISMISS_KEY = 'erp.totp-dismissed'
const needSetup = computed(() =>
  auth.isAuthenticated
  && (auth.user?.is_platform_admin || auth.user?.role === 'admin')
  && !auth.setupDismissed
  && !localStorage.getItem(DISMISS_KEY)
  && auth.totpSetupNeeded)

async function startSetup() {
  busy.value = true
  error.value = ''
  try {
    const setup = await auth.totpSetup()
    secret.value = setup.secret
    otpauthUri.value = setup.otpauth_uri
    qrDataUrl.value = await QRCode.toDataURL(setup.otpauth_uri, {
      width: 220, margin: 1,
    })
    step.value = 'qr'
  } catch (e) {
    toast.apiError(e)
  } finally {
    busy.value = false
  }
}

function openWizard() {
  open.value = true
  if (step.value === 'qr' && !secret.value) {
    void startSetup()
  }
}

async function confirmCode() {
  if (busy.value || code.value.trim().length !== 6) return
  busy.value = true
  error.value = ''
  try {
    const result = await auth.totpConfirm(code.value.trim())
    backupCodes.value = result.backup_codes
    step.value = 'codes'
  } catch (e) {
    error.value = (e as { status?: number }).status === 401
      ? t('mfa.wrongCode')
      : t('errors.unknown')
  } finally {
    busy.value = false
  }
}

function finish() {
  open.value = false
  auth.totpSetupNeeded = false
  localStorage.removeItem(DISMISS_KEY)
  toast.success(t('mfa.enabledToast'))
}

function closeWizard() {
  open.value = false
  auth.setupDismissed = true
  localStorage.setItem(DISMISS_KEY, '1')
}

watch(needSetup, (needed) => {
  if (needed) openWizard()
}, { immediate: true })
onMounted(() => { if (needSetup.value) openWizard() })
</script>

<template>
  <Dialog :open="open" :title="t('mfa.wizardTitle')" width="520px"
          @update:open="(v: boolean) => { if (!v) closeWizard() }">
    <!-- Шаг 1: QR -->
    <div v-if="step === 'qr'" class="space-y-4">
      <div class="flex items-start gap-3">
        <span class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-violet-100 text-violet-600 dark:bg-violet-950/60 dark:text-violet-400">
          <ShieldCheck class="h-5 w-5" />
        </span>
        <p class="text-sm leading-relaxed text-muted-foreground">
          {{ t('mfa.wizardIntro') }}
        </p>
      </div>
      <div v-if="qrDataUrl" class="flex flex-col items-center gap-2">
        <img :src="qrDataUrl" alt="QR" class="rounded-lg border border-zinc-200 dark:border-zinc-700" width="220" height="220">
        <p class="text-xs text-muted-foreground">{{ t('mfa.manualSecret') }}:</p>
        <code class="break-all rounded bg-zinc-100 px-2 py-1 text-xs dark:bg-zinc-800">{{ secret }}</code>
      </div>
      <div v-else class="py-10 text-center text-sm text-muted-foreground">{{ t('mfa.loading') }}</div>
      <div class="flex justify-end gap-2">
        <Button variant="outline" size="sm" @click="closeWizard">{{ t('mfa.later') }}</Button>
        <Button variant="emerald" size="sm" :disabled="!qrDataUrl" @click="step = 'code'">
          {{ t('mfa.scanned') }}
        </Button>
      </div>
    </div>

    <!-- Шаг 2: код подтверждения -->
    <form v-else-if="step === 'code'" class="space-y-4" @submit.prevent="confirmCode">
      <p class="text-sm text-muted-foreground">{{ t('mfa.enterCode') }}</p>
      <div class="space-y-1.5">
        <Label class="text-xs font-medium">{{ t('mfa.codeLabel') }}</Label>
        <Input v-model="code" placeholder="000000" class="text-center text-lg tracking-[0.4em]" />
        <p v-if="error" class="text-xs font-medium text-red-600 dark:text-red-400">{{ error }}</p>
      </div>
      <div class="flex justify-end gap-2">
        <Button variant="outline" size="sm" type="button" @click="step = 'qr'">{{ t('mfa.backButton') }}</Button>
        <Button variant="emerald" size="sm" type="submit" :disabled="busy || code.trim().length !== 6">
          <KeyRound class="mr-1.5 h-3.5 w-3.5" /> {{ t('mfa.confirm') }}
        </Button>
      </div>
    </form>

    <!-- Шаг 3: резервные коды (показ один раз) -->
    <div v-else class="space-y-4">
      <p class="text-sm font-medium text-red-600 dark:text-red-400">{{ t('mfa.saveNowWarning') }}</p>
      <div class="grid grid-cols-2 gap-1.5 rounded-lg bg-zinc-100 p-3 dark:bg-zinc-800">
        <code v-for="c in backupCodes" :key="c" class="text-center text-sm tracking-widest">{{ c }}</code>
      </div>
      <p class="text-xs text-muted-foreground">{{ t('mfa.backupHint') }}</p>
      <div class="flex justify-end">
        <Button variant="emerald" size="sm" @click="finish">{{ t('mfa.savedDone') }}</Button>
      </div>
    </div>
  </Dialog>
</template>

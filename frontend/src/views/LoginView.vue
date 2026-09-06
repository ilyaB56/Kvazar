<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import {
  AlertCircle,
  Boxes,
  BrainCircuit,
  Briefcase,
  Eye,
  EyeOff,
  Loader2,
  Lock,
  LogIn,
  Mail,
  PlugZap,
} from 'lucide-vue-next'
import QuasarMark from '../components/brand/QuasarMark.vue'
import Label from '../components/ui/Label.vue'
import { useAuthStore, type HttpError } from '../stores/auth'

// Вход-лендинг по эталону login-screen.tsx (редизайн §8.B): сплит
// 1.15fr/1fr — слева тёмная бренд-панель Квазара со слоганом и карточками
// преимуществ (статичные данные о продукте), справа форма входа.
// Макетные элементы эталона (демо-подсказка, brandStats, «Забыли пароль?»,
// «Код из приложения», «Демо-вход гостем», бейдж «2FA включена») не
// переносим: под них нет эндпоинтов. Поле кода 2FA — задел P1, скрыто.

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const features = computed(() => [
  { icon: PlugZap, title: t('login.feature1Title'), desc: t('login.feature1Desc') },
  { icon: BrainCircuit, title: t('login.feature2Title'), desc: t('login.feature2Desc') },
  { icon: Boxes, title: t('login.feature3Title'), desc: t('login.feature3Desc') },
  { icon: Briefcase, title: t('login.feature4Title'), desc: t('login.feature4Desc') },
])

const form = reactive({ email: '', password: '' })
const showPassword = ref(false)
const remember = ref(true)
const loading = ref(false)
const error = ref('')

// 429-блокировка: локальный таймер по Retry-After (порог 5 неудач / 60 с)
const lockRemaining = ref(0)
let lockTimer: number | undefined

function startLock(seconds: number) {
  lockRemaining.value = seconds
  window.clearInterval(lockTimer)
  lockTimer = window.setInterval(() => {
    lockRemaining.value -= 1
    if (lockRemaining.value <= 0) {
      window.clearInterval(lockTimer)
      lockTimer = undefined
      error.value = ''
    }
  }, 1000)
}
onBeforeUnmount(() => window.clearInterval(lockTimer))

function redirectTarget(): string {
  const redirect = route.query.redirect
  if (typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')) {
    return redirect
  }
  // посадочная по правам: первый доступный раздел (учёт → интеграции → ИИ …)
  return auth.firstAvailableRoute()
}

async function submit() {
  if (loading.value || lockRemaining.value > 0) return
  if (!form.email.trim() || !form.password.trim()) {
    error.value = t('login.errorEmpty')
    return
  }
  error.value = ''
  loading.value = true
  try {
    await auth.login(form.email.trim(), form.password, remember.value)
    await router.push(redirectTarget())
  } catch (e) {
    const http = e as HttpError
    if (http.status === 429) {
      startLock(http.retryAfter ?? 60)
    } else if (http.status === 401) {
      error.value = t('login.errorCredentials')
    } else {
      error.value = t('errors.unknown')
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen bg-background lg:grid lg:grid-cols-[1.15fr_1fr]">
    <!-- Левая брендовая панель (desktop) -->
    <aside class="relative hidden overflow-hidden bg-zinc-950 lg:flex lg:flex-col">
      <!-- декоративный фон: сетка + свечения -->
      <div
        aria-hidden
        class="pointer-events-none absolute inset-0 opacity-[0.16]"
        :style="{
          backgroundImage:
            'linear-gradient(to right, rgb(255 255 255 / 0.35) 1px, transparent 1px), linear-gradient(to bottom, rgb(255 255 255 / 0.35) 1px, transparent 1px)',
          backgroundSize: '44px 44px',
          maskImage: 'radial-gradient(ellipse 90% 80% at 50% 40%, black 30%, transparent 75%)',
          WebkitMaskImage: 'radial-gradient(ellipse 90% 80% at 50% 40%, black 30%, transparent 75%)',
        }"
      />
      <div aria-hidden class="pointer-events-none absolute -left-24 top-1/4 h-96 w-96 rounded-full bg-emerald-500/20 blur-[110px]" />
      <div aria-hidden class="pointer-events-none absolute -right-16 bottom-0 h-80 w-80 rounded-full bg-teal-500/15 blur-[100px]" />
      <div aria-hidden class="pointer-events-none absolute -top-12 right-1/4 h-72 w-72 rounded-full bg-violet-500/25 blur-[100px]" />

      <div class="relative flex items-center gap-2.5 px-10 py-8">
        <span class="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-xl bg-zinc-950 shadow-lg shadow-violet-500/25 ring-1 ring-white/15">
          <QuasarMark class="h-8 w-8" />
        </span>
        <div class="leading-tight">
          <p class="text-base font-bold tracking-tight text-white">{{ t('brand.name') }}</p>
          <p class="text-xs text-zinc-400">{{ t('login.tagline') }}</p>
        </div>
      </div>

      <div class="relative flex-1 px-10 py-6">
        <!-- max-w-2xl: слоган гарантированно в 2 строки (строка градиента 588px при 36px) -->
        <h1 class="max-w-2xl text-3xl font-bold leading-tight tracking-tight text-white xl:text-4xl login-rise">
          {{ t('login.slogan1') }}
          <span class="block bg-gradient-to-r from-violet-300 to-fuchsia-400 bg-clip-text text-transparent">{{ t('login.slogan2') }}</span>
        </h1>
        <p class="mt-3 max-w-md text-sm leading-relaxed text-zinc-400 login-rise login-rise-slow">
          {{ t('login.description') }}
        </p>

        <div class="mt-8 grid max-w-lg gap-3 sm:grid-cols-2">
          <div
            v-for="f in features"
            :key="f.title"
            class="rounded-xl border border-zinc-800/80 bg-zinc-900/60 p-4 backdrop-blur-sm transition-colors hover:border-violet-500/30 login-rise login-rise-card"
          >
            <span class="flex h-8 w-8 items-center justify-center rounded-lg bg-violet-500/15 text-violet-300">
              <component :is="f.icon" class="h-4 w-4" />
            </span>
            <p class="mt-2.5 text-sm font-semibold text-zinc-100">{{ f.title }}</p>
            <p class="mt-1 text-xs leading-relaxed text-zinc-400">{{ f.desc }}</p>
          </div>
        </div>
      </div>
    </aside>

    <!-- Правая панель: форма входа -->
    <main class="relative flex min-h-screen flex-col items-center justify-center px-4 py-10 sm:px-8">
      <!-- мобильный логотип -->
      <div class="mb-8 flex items-center gap-2.5 lg:hidden">
        <span class="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-xl shadow-lg shadow-violet-500/25 ring-1 ring-zinc-200 dark:ring-zinc-700">
          <QuasarMark class="h-8 w-8" />
        </span>
        <div class="leading-tight">
          <p class="text-base font-bold tracking-tight">{{ t('brand.name') }}</p>
          <p class="text-xs text-muted-foreground">{{ t('login.tagline') }}</p>
        </div>
      </div>

      <div class="w-full max-w-[400px] login-rise">
        <div class="rounded-2xl border border-zinc-200 bg-card p-6 shadow-xl shadow-zinc-950/5 sm:p-8 dark:border-zinc-800 dark:shadow-black/20">
          <h2 class="text-xl font-bold tracking-tight">{{ t('login.title') }}</h2>
          <p class="mt-1 text-sm text-muted-foreground">{{ t('login.subtitle') }}</p>

          <!-- Задел 2FA (P1): поле «Код из приложения-аутентификатора»
               появляется здесь, над «Запомнить меня», когда у пользователя
               включён TOTP; форма отправляет otp-код вместе с паролем. -->

          <form class="mt-5 space-y-4" @submit.prevent="submit">
            <div class="space-y-1.5">
              <Label for="login-email" class="text-xs font-medium">{{ t('login.email') }}</Label>
              <div class="relative">
                <Mail class="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  id="login-email" v-model="form.email" type="email" autocomplete="email"
                  :placeholder="t('login.emailPlaceholder')"
                  class="flex h-10 w-full rounded-lg border border-input bg-transparent py-1 pl-9 pr-3 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"
                >
              </div>
            </div>

            <div class="space-y-1.5">
              <Label for="login-password" class="text-xs font-medium">{{ t('login.password') }}</Label>
              <div class="relative">
                <Lock class="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  id="login-password" v-model="form.password"
                  :type="showPassword ? 'text' : 'password'" autocomplete="current-password"
                  :placeholder="t('login.passwordPlaceholder')"
                  class="flex h-10 w-full rounded-lg border border-input bg-transparent py-1 pl-9 pr-10 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                <button
                  type="button"
                  class="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
                  :aria-label="showPassword ? t('login.hidePassword') : t('login.showPassword')"
                  @click="showPassword = !showPassword"
                >
                  <EyeOff v-if="showPassword" class="h-4 w-4" />
                  <Eye v-else class="h-4 w-4" />
                </button>
              </div>
            </div>

            <p
              v-if="error || lockRemaining > 0"
              role="alert"
              class="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 dark:border-red-900 dark:bg-red-950/40"
            >
              <AlertCircle class="mt-0.5 h-4 w-4 shrink-0 text-red-600 dark:text-red-400" />
              <span class="text-xs font-medium text-red-700 dark:text-red-400">
                {{ lockRemaining > 0 ? t('login.errorRateLimited', { seconds: lockRemaining }) : error }}
              </span>
            </p>

            <label class="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
              <input
                v-model="remember" type="checkbox"
                class="h-4 w-4 rounded border-zinc-300 accent-emerald-600 dark:border-zinc-700"
              >
              {{ t('login.remember') }}
            </label>

            <button
              type="submit"
              :disabled="loading || lockRemaining > 0"
              class="inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-emerald-600 text-base font-semibold text-white shadow-lg shadow-emerald-600/20 transition-colors hover:bg-emerald-700 disabled:pointer-events-none disabled:opacity-70"
            >
              <template v-if="loading">
                <Loader2 class="h-4 w-4 animate-spin" /> {{ t('login.submitting') }}
              </template>
              <template v-else>
                <LogIn class="h-4 w-4" /> {{ t('login.submit') }}
              </template>
            </button>
          </form>
        </div>

        <p class="mt-6 text-center text-xs text-muted-foreground">
          {{ t('login.copyright', { year: new Date().getFullYear() }) }}
        </p>
      </div>
    </main>
  </div>
</template>

<style scoped>
/* Появление блоков (аналог motion-анимаций эталона, без зависимостей) */
.login-rise {
  animation: login-rise 0.45s ease-out both;
}
.login-rise-slow {
  animation-delay: 0.08s;
}
.login-rise-card:nth-child(1) { animation-delay: 0.12s; }
.login-rise-card:nth-child(2) { animation-delay: 0.19s; }
.login-rise-card:nth-child(3) { animation-delay: 0.26s; }
.login-rise-card:nth-child(4) { animation-delay: 0.33s; }

@keyframes login-rise {
  from {
    opacity: 0;
    transform: translateY(14px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (prefers-reduced-motion: reduce) {
  .login-rise {
    animation: none;
  }
}
</style>

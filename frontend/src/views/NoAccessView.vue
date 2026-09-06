<script setup lang="ts">
// Хотфикс (реестр, приёмка этапа C): пользователь без единого доступного
// раздела раньше молча выбрасывался на /login. Этот экран объясняет, что
// произошло, и сам возвращает в систему, когда права появятся (опрос 60 с
// — тот же ритм, что и фильтрация навигации в оболочке).
import { onBeforeUnmount, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { LogOut, RefreshCw, ShieldOff } from 'lucide-vue-next'
import QuasarMark from '../components/brand/QuasarMark.vue'
import { Button } from '../components/ui'
import { useAuthStore } from '../stores/auth'

const { t } = useI18n()
const router = useRouter()
const auth = useAuthStore()

let timer: number | undefined

async function recheck() {
  await auth.fetchPermissions()
  const target = auth.firstAvailableRoute()
  if (target !== '/no-access') await router.push(target)
}

function logout() {
  auth.logout()
  void router.push({ name: 'login' })
}

onMounted(() => {
  void recheck()
  timer = window.setInterval(() => { void recheck() }, 60_000)
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <div class="flex min-h-screen flex-col items-center justify-center bg-background px-4">
    <div class="flex items-center gap-2.5">
      <span class="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-xl shadow-lg shadow-violet-500/25 ring-1 ring-zinc-200 dark:ring-zinc-700">
        <QuasarMark class="h-8 w-8" />
      </span>
      <p class="text-base font-bold tracking-tight">{{ t('brand.name') }}</p>
    </div>

    <div class="mt-8 w-full max-w-md rounded-2xl border border-zinc-200 bg-card p-6 text-center shadow-xl shadow-zinc-950/5 sm:p-8 dark:border-zinc-800 dark:shadow-black/20">
      <span class="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-amber-50 text-amber-600 dark:bg-amber-950/60 dark:text-amber-400">
        <ShieldOff class="h-6 w-6" />
      </span>
      <h1 class="mt-4 text-xl font-bold tracking-tight">{{ t('noAccess.title') }}</h1>
      <p class="mt-2 text-sm leading-relaxed text-muted-foreground">
        {{ t('noAccess.description') }}
      </p>
      <p class="mt-1 text-xs text-muted-foreground">
        {{ t('noAccess.hint', { email: auth.userName }) }}
      </p>
      <div class="mt-6 flex justify-center gap-2">
        <Button variant="outline" class="gap-1.5" @click="recheck">
          <RefreshCw class="h-4 w-4" /> {{ t('noAccess.recheck') }}
        </Button>
        <Button variant="ghost" class="gap-1.5" @click="logout">
          <LogOut class="h-4 w-4" /> {{ t('noAccess.logout') }}
        </Button>
      </div>
    </div>
  </div>
</template>

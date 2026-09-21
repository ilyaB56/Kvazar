<script setup lang="ts">
// Диалог «пароль сброшен»: временный пароль показан один раз + «Скопировать».
// Общий для сброса сотрудником (Настройки → Пользователи) и админа
// организации (экран выбора организации).
import { useI18n } from 'vue-i18n'
import Button from './Button.vue'
import Dialog from './Dialog.vue'
import { useToast } from './Toast'

defineProps<{
  open: boolean
  title: string
  hint?: string
  tempPassword: string | null
}>()
const emit = defineEmits<{ 'update:open': [value: boolean] }>()

const { t } = useI18n()
const toast = useToast()

async function copy(value: string) {
  try {
    await navigator.clipboard.writeText(value)
    toast.success(t('connections.copied'))
  } catch {
    toast.error(t('connections.copyFailed'))
  }
}
</script>

<template>
  <Dialog
    :open="open" :title="title"
    @update:open="(v: boolean) => { if (!v) emit('update:open', false) }"
  >
    <div class="space-y-4">
      <p v-if="hint" class="text-sm text-muted-foreground">{{ hint }}</p>
      <p class="text-xs font-medium text-red-600 dark:text-red-400">
        {{ t('mt.tempPasswordOnce') }}
      </p>
      <div class="flex items-center gap-2 rounded-lg bg-zinc-100 p-2 dark:bg-zinc-800">
        <code class="flex-1 break-all text-sm">{{ tempPassword }}</code>
        <Button variant="outline" size="sm" @click="tempPassword && copy(tempPassword)">
          {{ t('security.copy') }}
        </Button>
      </div>
      <div class="flex justify-end">
        <Button variant="outline" size="sm" @click="emit('update:open', false)">
          {{ t('mt.done') }}
        </Button>
      </div>
    </div>
  </Dialog>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { post } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { Button, Dialog, Input, Label, useToast } from '../components/ui'

const { t } = useI18n()
const auth = useAuthStore()
const toast = useToast()

const open = defineModel<boolean>({ default: false })
watch(open, (value) => { if (value) reset() })

const form = reactive({ old_password: '', new_password: '', confirm: '' })
const submitting = ref(false)

function reset() {
  form.old_password = ''
  form.new_password = ''
  form.confirm = ''
}

async function submit() {
  if (!form.old_password || !form.new_password) {
    toast.error(t('password.required'))
    return
  }
  if (form.new_password !== form.confirm) {
    toast.error(t('password.mismatch'))
    return
  }
  submitting.value = true
  try {
    await post('/auth/change-password', {
      old_password: form.old_password,
      new_password: form.new_password,
    })
    open.value = false
    toast.success(t('password.changed'))
    auth.logout()
    window.location.assign('/login')
  } catch (error) {
    toast.apiError(error)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <Dialog :open="open" :title="t('password.title')" width="26rem" @update:open="open = $event">
    <form class="space-y-4" @submit.prevent="submit">
      <div class="space-y-1.5">
        <Label for="cp-old">{{ t('password.old') }}</Label>
        <Input id="cp-old" v-model="form.old_password" type="password" autocomplete="current-password" />
      </div>
      <div class="space-y-1.5">
        <Label for="cp-new">{{ t('password.new') }}</Label>
        <Input id="cp-new" v-model="form.new_password" type="password" autocomplete="new-password" />
      </div>
      <div class="space-y-1.5">
        <Label for="cp-confirm">{{ t('password.confirm') }}</Label>
        <Input id="cp-confirm" v-model="form.confirm" type="password" autocomplete="new-password" />
      </div>
      <p class="text-xs text-muted-foreground">{{ t('password.rules') }}</p>
      <div class="flex justify-end gap-2">
        <Button variant="outline" type="button" @click="open = false">{{ t('password.cancel') }}</Button>
        <Button variant="emerald" type="submit" :disabled="submitting">{{ t('password.submit') }}</Button>
      </div>
    </form>
  </Dialog>
</template>

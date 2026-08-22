<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { useAuthStore } from '../stores/auth'

const { t } = useI18n()
const router = useRouter()
const auth = useAuthStore()

const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ email: '', password: '' })

const rules: FormRules = {
  email: [{ required: true, message: () => t('connections.fieldRequired'), trigger: 'blur' }],
  password: [{ required: true, message: () => t('connections.fieldRequired'), trigger: 'blur' }],
}

async function submit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  loading.value = true
  try {
    await auth.login(form.email, form.password)
    router.push({ name: 'connections' })
  } catch (error) {
    const status = error instanceof Error && error.message.includes('401')
    ElMessage.error(status ? t('login.badCredentials') : t('errors.unknown'))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login">
    <el-card class="login__card">
      <h1 class="login__title">{{ t('login.title') }}</h1>
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @keyup.enter="submit">
        <el-form-item :label="t('login.email')" prop="email">
          <el-input v-model="form.email" autocomplete="username" />
        </el-form-item>
        <el-form-item :label="t('login.password')" prop="password">
          <el-input v-model="form.password" type="password" autocomplete="current-password" show-password />
        </el-form-item>
        <el-button type="primary" class="login__submit" :loading="loading" @click="submit">
          {{ t('login.submit') }}
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.login {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.login__card {
  width: 380px;
}

.login__title {
  margin: 0 0 16px;
  font-size: 18px;
  text-align: center;
}

.login__submit {
  width: 100%;
}
</style>

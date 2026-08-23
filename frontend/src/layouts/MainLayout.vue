<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { FormInstance } from 'element-plus'
import { ArrowDown, Connection, Cpu, Histogram, Key, Monitor, Refresh, User as UserIcon } from '@element-plus/icons-vue'
import { post } from '../api/client'
import { useAuthStore } from '../stores/auth'

const { t } = useI18n()
const router = useRouter()
const auth = useAuthStore()

async function logout() {
  await auth.apiLogout()
  auth.logout()
  router.push({ name: 'login' })
}

// ----- Смена пароля (security-p0): все сессии умирают, редирект на логин -----
const pwdDialogVisible = ref(false)
const pwdSubmitting = ref(false)
const pwdFormRef = ref<FormInstance>()
const pwdForm = reactive({ old_password: '', new_password: '', confirm: '' })

function openChangePassword() {
  pwdForm.old_password = ''
  pwdForm.new_password = ''
  pwdForm.confirm = ''
  pwdDialogVisible.value = true
}

async function submitChangePassword() {
  const valid = await pwdFormRef.value?.validate().catch(() => false)
  if (!valid) return
  if (pwdForm.new_password !== pwdForm.confirm) {
    ElMessage.error(t('password.mismatch'))
    return
  }
  pwdSubmitting.value = true
  try {
    await post('/auth/change-password', {
      old_password: pwdForm.old_password,
      new_password: pwdForm.new_password,
    })
    pwdDialogVisible.value = false
    ElMessage.success(t('password.changed'))
    auth.logout()
    router.push({ name: 'login' })
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('errors.unknown'))
  } finally {
    pwdSubmitting.value = false
  }
}
</script>

<template>
  <el-container class="layout">
    <el-aside width="220px" class="layout__aside">
      <div class="layout__logo">{{ t('app.title') }}</div>
      <el-menu router :default-active="$route.path" class="layout__menu">
        <el-menu-item index="/connections">
          <el-icon><Connection /></el-icon>
          <span>{{ t('nav.connections') }}</span>
        </el-menu-item>
        <el-menu-item index="/sync">
          <el-icon><Refresh /></el-icon>
          <span>{{ t('nav.sync') }}</span>
        </el-menu-item>
        <el-menu-item v-if="auth.user?.role === 'admin'" index="/system">
          <el-icon><Monitor /></el-icon>
          <span>{{ t('nav.system') }}</span>
        </el-menu-item>
        <el-menu-item disabled>
          <el-icon><Histogram /></el-icon>
          <span>{{ t('nav.accounting') }} — {{ t('app.soon') }}</span>
        </el-menu-item>
        <el-menu-item disabled>
          <el-icon><UserIcon /></el-icon>
          <span>{{ t('nav.crm') }} — {{ t('app.soon') }}</span>
        </el-menu-item>
        <el-menu-item disabled>
          <el-icon><Cpu /></el-icon>
          <span>{{ t('nav.ai') }} — {{ t('app.soon') }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="layout__header">
        <span />
        <el-dropdown>
          <span class="layout__user">
            <el-icon><UserIcon /></el-icon>
            <span class="layout__username">{{ auth.userName }}</span>
            <el-icon><ArrowDown /></el-icon>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item :icon="Key" @click="openChangePassword">
                {{ t('user.changePassword') }}
              </el-dropdown-item>
              <el-dropdown-item divided @click="logout">
                {{ t('app.logout') }}
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-header>
      <el-main class="layout__main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>

  <el-dialog v-model="pwdDialogVisible" :title="t('password.title')" width="420px">
    <el-form ref="pwdFormRef" :model="pwdForm" label-position="top" @keyup.enter="submitChangePassword">
      <el-form-item :label="t('password.old')" required>
        <el-input v-model="pwdForm.old_password" type="password" show-password autocomplete="current-password" />
      </el-form-item>
      <el-form-item :label="t('password.new')" required>
        <el-input v-model="pwdForm.new_password" type="password" show-password autocomplete="new-password" />
      </el-form-item>
      <el-form-item :label="t('password.confirm')" required>
        <el-input v-model="pwdForm.confirm" type="password" show-password autocomplete="new-password" />
      </el-form-item>
      <div class="muted">{{ t('password.rules') }}</div>
    </el-form>
    <template #footer>
      <el-button @click="pwdDialogVisible = false">{{ t('password.cancel') }}</el-button>
      <el-button type="primary" :loading="pwdSubmitting" @click="submitChangePassword">
        {{ t('password.submit') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.layout {
  height: 100%;
}

.layout__aside {
  border-right: 1px solid #e4e7ed;
  background: #fff;
}

.layout__logo {
  padding: 18px 20px;
  font-size: 18px;
  font-weight: 600;
}

.layout__menu {
  border-right: none;
}

.layout__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
}

.layout__user {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  outline: none;
}

.layout__username {
  font-weight: 600;
}

.layout__main {
  padding: 0;
}
</style>

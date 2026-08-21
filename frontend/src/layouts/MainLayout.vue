<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { Connection, Cpu, Histogram, Refresh, SwitchButton, User as UserIcon } from '@element-plus/icons-vue'
import { useAuthStore } from '../stores/auth'

const { t } = useI18n()
const router = useRouter()
const auth = useAuthStore()

function logout() {
  auth.logout()
  router.push({ name: 'login' })
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
        <div class="layout__user">
          <el-icon><UserIcon /></el-icon>
          <span class="layout__username">{{ auth.userName }}</span>
          <el-button :icon="SwitchButton" link type="primary" @click="logout">
            {{ t('app.logout') }}
          </el-button>
        </div>
      </el-header>
      <el-main class="layout__main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
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
}

.layout__username {
  font-weight: 600;
}

.layout__main {
  padding: 0;
}
</style>

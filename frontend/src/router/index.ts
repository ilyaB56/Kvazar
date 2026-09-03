import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { i18n } from '../i18n'
import ErpShell from '../layouts/ErpShell.vue'
import IntegrationsLayout from '../layouts/IntegrationsLayout.vue'
import SettingsLayout from '../layouts/SettingsLayout.vue'
import ConnectionsView from '../views/ConnectionsView.vue'
import SyncView from '../views/SyncView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('../views/LoginView.vue') },
    {
      path: '/',
      component: ErpShell,
      children: [
        { path: '', redirect: '/integrations/connections' },
        // Система → Интеграции (подразделы, §5)
        {
          path: 'integrations',
          component: IntegrationsLayout,
          meta: { title: 'Интеграции', crumb: 'Подключения и синхронизации' },
          children: [
            { path: '', redirect: '/integrations/connections' },
            {
              path: 'connections',
              name: 'connections',
              component: ConnectionsView,
              meta: { requiresAuth: true, title: 'Интеграции', crumb: 'Подключения' },
            },
            {
              path: 'sync',
              name: 'sync',
              component: SyncView,
              meta: { requiresAuth: true, title: 'Интеграции', crumb: 'Синхронизации' },
            },
            {
              path: 'notifications',
              name: 'notifications',
              component: () => import('../views/NotificationsView.vue'),
              meta: { requiresAuth: true, requiresAdmin: true, title: 'Интеграции', crumb: 'Уведомления' },
            },
          ],
        },
        // Система → Настройки (подразделы, §5; расширение — этап G)
        {
          path: 'settings',
          component: SettingsLayout,
          children: [
            { path: '', redirect: '/settings/system' },
            {
              path: 'system',
              name: 'system',
              component: () => import('../views/SystemView.vue'),
              meta: { requiresAuth: true, requiresAdmin: true, title: 'Настройки', crumb: 'Система' },
            },
          ],
        },
        {
          path: 'assistant',
          name: 'assistant',
          component: () => import('../views/AssistantView.vue'),
          meta: { requiresAuth: true, requiresAdmin: true, title: 'ИИ-ассистент', crumb: 'Диалоги и предложения' },
        },
      ],
    },
    // Старые маршруты — редиректы (§5)
    { path: '/connections', redirect: '/integrations/connections' },
    { path: '/sync', redirect: '/integrations/sync' },
    { path: '/notifications', redirect: '/integrations/notifications' },
    { path: '/system', redirect: '/settings/system' },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.matched.some((record) => record.meta.requiresAuth) && !auth.isAuthenticated) {
    return { name: 'login' }
  }
  if (to.matched.some((record) => record.meta.requiresAdmin) && auth.user && auth.user.role !== 'admin') {
    return { path: '/integrations/connections' }
  }
  if (to.name === 'login' && auth.isAuthenticated) {
    return { path: '/integrations/connections' }
  }
  if (auth.isAuthenticated && !auth.user) {
    try {
      await auth.fetchMe()
    } catch {
      auth.logout()
      return { name: 'login' }
    }
  }
  // заголовок раздела шапки — по meta.title; i18n.global — guard вне setup-контекста
  document.title = to.meta.title
    ? `${to.meta.title} · ${i18n.global.t('brand.name')}`
    : String(i18n.global.t('brand.name'))
})

export default router

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
    { path: '/no-access', name: 'no-access', component: () => import('../views/NoAccessView.vue') },
    {
      path: '/',
      component: ErpShell,
      children: [
        { path: '', redirect: '/dashboard' },
        {
          path: 'dashboard',
          name: 'dashboard',
          component: () => import('../views/DashboardView.vue'),
          meta: { requiresAuth: true, title: 'Дашборд', crumb: 'Сводка контура' },
        },
        {
          path: 'reports',
          name: 'reports',
          component: () => import('../views/ReportsView.vue'),
          meta: { requiresAuth: true, title: 'Отчёты', crumb: 'Аналитика' },
        },
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
              meta: {
                requiresAuth: true, module: 'integrations', level: 'ro',
                title: 'Интеграции', crumb: 'Подключения',
              },
            },
            {
              path: 'sync',
              name: 'sync',
              component: SyncView,
              meta: {
                requiresAuth: true, module: 'integrations', level: 'ro',
                title: 'Интеграции', crumb: 'Синхронизации',
              },
            },
            {
              path: 'notifications',
              name: 'notifications',
              component: () => import('../views/NotificationsView.vue'),
              meta: {
                requiresAuth: true, requiresAdmin: true,
                module: 'integrations', level: 'ro',
                title: 'Интеграции', crumb: 'Уведомления',
              },
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
            {
              path: 'permissions',
              name: 'permissions',
              component: () => import('../views/PermissionsView.vue'),
              meta: { requiresAuth: true, requiresAdmin: true, title: 'Настройки', crumb: 'Доступы и роли' },
            },
          ],
        },
        {
          path: 'accounting',
          name: 'finance',
          component: () => import('../views/FinanceView.vue'),
          meta: {
            requiresAuth: true, module: 'accounting', level: 'ro',
            title: 'Финансы', crumb: 'Счета и платежи',
          },
        },
        {
          path: 'crm',
          name: 'crm',
          component: () => import('../views/CrmView.vue'),
          meta: {
            requiresAuth: true, module: 'crm', level: 'ro',
            title: 'Продажи', crumb: 'Сделки и коммуникации',
          },
        },
        {
          path: 'assistant',
          name: 'assistant',
          component: () => import('../views/AssistantView.vue'),
          // доступ — по правам ai (§6.3): rw у user по сиду, readonly — ro
          meta: { requiresAuth: true, module: 'ai', level: 'ro', title: 'ИИ-ассистент', crumb: 'Диалоги и предложения' },
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
  if (auth.isAuthenticated && !auth.user) {
    try {
      await auth.fetchMe()
    } catch {
      auth.logout()
      return { name: 'login' }
    }
  }
  if (auth.isAuthenticated && !auth.permissions) {
    // guard дожидается прав: прямой URL в закрытый модуль уходит редиректом
    await auth.fetchPermissions()
  }
  if (to.matched.some((record) => record.meta.requiresAdmin) && auth.user && auth.user.role !== 'admin') {
    return { path: auth.firstAvailableRoute() }
  }
  // права модуля (§6.3): пункт виден при уровне ≠ none; level 'ro' — только чтение
  const moduleRecord = to.matched.find((record) => record.meta.module)
  if (moduleRecord && auth.permissions) {
    const module = moduleRecord.meta.module as string
    const need = (moduleRecord.meta.level as string) ?? 'ro'
    const level = auth.permissions[module] ?? 'none'
    if (level === 'none' || (need === 'rw' && level !== 'rw')) {
      return { path: auth.firstAvailableRoute() }
    }
  }
  if (to.name === 'no-access' && !auth.isAuthenticated) {
    return { name: 'login' }
  }
  if (to.name === 'login' && auth.isAuthenticated) {
    return { path: auth.firstAvailableRoute() }
  }
  // заголовок раздела шапки — по meta.title; i18n.global — guard вне setup-контекста
  document.title = to.meta.title
    ? `${to.meta.title} · ${i18n.global.t('brand.name')}`
    : String(i18n.global.t('brand.name'))
})

export default router

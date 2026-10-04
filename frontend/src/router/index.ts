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
      // восстановление пароля по email-ссылке (multitenancy §7.5) — публично
      path: '/reset-password', name: 'reset-password',
      component: () => import('../views/ResetPasswordView.vue'),
      meta: { titleKey: 'recovery.title' },
    },
    {
      // публичная заявка на подключение организации (self-service)
      path: '/signup', name: 'signup',
      component: () => import('../views/SignupView.vue'),
      meta: { titleKey: 'signup.title' },
    },
    {
      // подтверждение email заявки по ссылке из письма (24 часа) — публично
      path: '/signup/verify', name: 'signup-verify',
      component: () => import('../views/SignupVerifyView.vue'),
      meta: { titleKey: 'signupVerify.pageTitle' },
    },
    {
      // multitenancy: выбор организации супер-админом платформы (§9)
      path: '/select-org', name: 'select-org',
      component: () => import('../views/SelectOrgView.vue'),
      meta: { requiresAuth: true, titleKey: 'mt.selectOrgTitle' },
    },
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
          meta: { requiresAuth: true, titleKey: 'route.dashboard', crumbKey: 'route.dashboardCrumb' },
        },
        {
          path: 'reports',
          name: 'reports',
          component: () => import('../views/ReportsView.vue'),
          meta: { requiresAuth: true, titleKey: 'route.reports', crumbKey: 'route.reportsCrumb' },
        },
        // Система → Интеграции (подразделы, §5)
        {
          path: 'integrations',
          component: IntegrationsLayout,
          meta: { titleKey: 'route.integrations', crumbKey: 'route.integrationsCrumb' },
          children: [
            { path: '', redirect: '/integrations/overview' },
            {
              path: 'overview',
              name: 'integrations-overview',
              component: () => import('../views/IntegrationsCoreView.vue'),
              meta: {
                requiresAuth: true, module: 'integrations', level: 'ro',
                titleKey: 'route.integrations', crumbKey: 'route.integrationsCoreCrumb',
              },
            },
            {
              path: 'connections',
              name: 'connections',
              component: ConnectionsView,
              meta: {
                requiresAuth: true, module: 'integrations', level: 'ro',
                titleKey: 'route.integrations', crumbKey: 'route.integrationsConnCrumb',
              },
            },
            {
              path: 'sync',
              name: 'sync',
              component: SyncView,
              meta: {
                requiresAuth: true, module: 'integrations', level: 'ro',
                titleKey: 'route.integrations', crumbKey: 'route.integrationsSyncCrumb',
              },
            },
            {
              path: 'notifications',
              name: 'notifications',
              component: () => import('../views/NotificationsView.vue'),
              meta: {
                requiresAuth: true, requiresAdmin: true,
                module: 'integrations', level: 'ro',
                titleKey: 'route.integrations', crumbKey: 'route.integrationsNotifCrumb',
              },
            },
          ],
        },
        // Система → Настройки (подразделы, §5; расширение — этап G)
        {
          path: 'settings',
          component: SettingsLayout,
          children: [
            { path: '', redirect: '/settings/organization' },
            {
              path: 'organization',
              name: 'organization',
              component: () => import('../views/OrganizationView.vue'),
              meta: { requiresAuth: true, titleKey: 'route.settings', crumbKey: 'route.settingsOrgCrumb' },
            },
            {
              path: 'profile',
              name: 'profile',
              component: () => import('../views/ProfileView.vue'),
              meta: { requiresAuth: true, titleKey: 'route.settings', crumbKey: 'route.settingsProfileCrumb' },
            },
            {
              path: 'security',
              name: 'security',
              component: () => import('../views/SecurityView.vue'),
              meta: { requiresAuth: true, titleKey: 'route.settings', crumbKey: 'route.settingsSecurityCrumb' },
            },
            {
              path: 'system',
              name: 'system',
              component: () => import('../views/SystemView.vue'),
              meta: { requiresAuth: true, requiresAdmin: true, titleKey: 'route.settings', crumbKey: 'route.settingsSystemCrumb' },
            },
            {
              path: 'permissions',
              name: 'permissions',
              component: () => import('../views/PermissionsView.vue'),
              meta: { requiresAuth: true, requiresAdmin: true, titleKey: 'route.settings', crumbKey: 'route.settingsPermsCrumb' },
            },
            {
              // Пользователи и делегирование (role-delegation §7): доступ
              // admin ИЛИ обладатель rw ≥1 модуль; чисто-ro — пустое состояние
              path: 'delegation',
              name: 'delegation',
              component: () => import('../views/DelegationView.vue'),
              meta: { requiresAuth: true, titleKey: 'route.settings', crumbKey: 'route.settingsDelegCrumb' },
            },
          ],
        },
        {
          path: 'accounting',
          name: 'finance',
          component: () => import('../views/FinanceView.vue'),
          meta: {
            requiresAuth: true, module: 'accounting', level: 'ro',
            titleKey: 'route.finance', crumbKey: 'route.financeCrumb',
          },
        },
        {
          path: 'crm',
          component: () => import('../layouts/CrmLayout.vue'),
          children: [
            { path: '', redirect: '/crm/deals' },
            {
              path: 'deals',
              name: 'crm',
              component: () => import('../views/CrmView.vue'),
              meta: {
                requiresAuth: true, module: 'crm', level: 'ro',
                titleKey: 'route.crm', crumbKey: 'route.crmCrumb',
              },
            },
            {
              path: 'funnel',
              name: 'crm-funnel',
              component: () => import('../views/CrmKanbanView.vue'),
              meta: {
                requiresAuth: true, module: 'crm', level: 'ro',
                titleKey: 'route.crm', crumbKey: 'route.crmCrumb',
              },
            },
            {
              path: 'orders',
              name: 'crm-orders',
              component: () => import('../views/SalesOrdersView.vue'),
              meta: {
                requiresAuth: true, module: 'accounting', level: 'ro',
                titleKey: 'route.crm', crumbKey: 'route.crmOrdersCrumb',
              },
            },
          ],
        },
        {
          path: 'inventory',
          name: 'inventory',
          component: () => import('../views/InventoryView.vue'),
          meta: {
            requiresAuth: true, module: 'accounting', level: 'ro',
            titleKey: 'route.inventory', crumbKey: 'route.inventoryCrumb',
          },
        },
        {
          path: 'purchasing',
          name: 'purchasing',
          component: () => import('../views/PurchasingView.vue'),
          meta: {
            requiresAuth: true, module: 'accounting', level: 'ro',
            titleKey: 'route.purchasing', crumbKey: 'route.purchasingCrumb',
          },
        },
        {
          // Система → Инструменты: браузер таблиц (devtools §11, этап A);
          // доступ — table_browser, гейтится внутри экрана (право не
          // модульное, module-мету guard'а не используем)
          path: 'tools',
          name: 'tools',
          component: () => import('../views/ToolsView.vue'),
          meta: { requiresAuth: true, titleKey: 'route.tools', crumbKey: 'route.toolsCrumb' },
        },
        {
          path: 'assistant',
          name: 'assistant',
          component: () => import('../views/AssistantView.vue'),
          // доступ — по правам ai (§6.3): rw у user по сиду, readonly — ro
          meta: { requiresAuth: true, module: 'ai', level: 'ro', titleKey: 'route.assistant', crumbKey: 'route.assistantCrumb' },
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
    // супер-админ без выбранной организации — сначала экран выбора
    if (auth.tokenPl && !auth.tokenOrg) return { name: 'select-org' }
    return { path: auth.firstAvailableRoute() }
  }
  // платформенный контекст без org: данные недоступны (403 no_company_context)
  // — единственный рабочий экран супер-админа без организации — выбор/реестр
  if (auth.isAuthenticated && auth.tokenPl && !auth.tokenOrg
      && to.name !== 'select-org') {
    return { name: 'select-org' }
  }
  // заголовок раздела шапки — по meta.title; i18n.global — guard вне setup-контекста
  const titleKey = to.meta.titleKey as string | undefined
  document.title = titleKey
    ? `${i18n.global.t(titleKey)} · ${i18n.global.t('brand.name')}`
    : String(i18n.global.t('brand.name'))
})

export default router

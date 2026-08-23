import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import MainLayout from '../layouts/MainLayout.vue'
import ConnectionsView from '../views/ConnectionsView.vue'
import SyncView from '../views/SyncView.vue'
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('../views/LoginView.vue') },
    {
      path: '/',
      component: MainLayout,
      children: [
        { path: '', redirect: { name: 'connections' } },
        {
          path: 'connections',
          name: 'connections',
          component: ConnectionsView,
          meta: { requiresAuth: true },
        },
        { path: 'sync', name: 'sync', component: SyncView, meta: { requiresAuth: true } },
        {
          path: 'system',
          name: 'system',
          component: () => import('../views/SystemView.vue'),
          meta: { requiresAuth: true, requiresAdmin: true },
        },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.matched.some((record) => record.meta.requiresAuth) && !auth.isAuthenticated) {
    return { name: 'login' }
  }
  if (to.matched.some((record) => record.meta.requiresAdmin) && auth.user?.role !== 'admin') {
    return { name: 'connections' }
  }
  if (to.name === 'login' && auth.isAuthenticated) {
    return { name: 'connections' }
  }
  if (auth.isAuthenticated && !auth.user) {
    try {
      await auth.fetchMe()
    } catch {
      auth.logout()
      return { name: 'login' }
    }
  }
})

export default router

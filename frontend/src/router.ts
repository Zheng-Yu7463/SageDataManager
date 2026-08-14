import { createRouter, createWebHistory } from 'vue-router'

import AssetsView from '@/views/AssetsView.vue'
import ArchiveHealthView from '@/views/ArchiveHealthView.vue'
import PendingFilesView from '@/views/PendingFilesView.vue'
import ImportAssetsView from '@/views/ImportAssetsView.vue'
import AssetDetailView from '@/views/AssetDetailView.vue'
import AccountRegistrationView from '@/views/AccountRegistrationView.vue'
import ActivityLogView from '@/views/ActivityLogView.vue'
import ArchivedAssetsView from '@/views/ArchivedAssetsView.vue'
import DashboardView from '@/views/DashboardView.vue'
import SearchView from '@/views/SearchView.vue'
import NotFoundView from '@/views/NotFoundView.vue'
import SettingsView from '@/views/SettingsView.vue'
import { useBranding } from '@/composables/useBranding'

export const router = createRouter({
  history: createWebHistory(),
  scrollBehavior(_to, _from, savedPosition) {
    return savedPosition ?? { top: 0 }
  },
  routes: [
    { path: '/', name: 'dashboard', component: DashboardView, meta: { title: '科研资产总览' } },
    { path: '/register/:token', name: 'account-registration', component: AccountRegistrationView, meta: { title: '完成账号注册' } },
    { path: '/search', name: 'search', component: SearchView, meta: { title: '统一检索' } },
    { path: '/papers', name: 'papers', component: AssetsView, meta: { assetType: 'paper', title: '论文目录' } },
    { path: '/settings', name: 'settings', component: SettingsView, meta: { title: '系统设置' } },
    { path: '/archive-health', name: 'archive-health', component: ArchiveHealthView, meta: { title: '归档健康' } },
    { path: '/archived-assets', name: 'archived-assets', component: ArchivedAssetsView, meta: { title: '已归档资产' } },
    { path: '/import-assets', name: 'import-assets', component: ImportAssetsView, meta: { title: '批量导入资产' } },
    { path: '/unclaimed-files', name: 'unclaimed-files', component: PendingFilesView, meta: { title: '待认领文件' } },
    { path: '/activity-log', name: 'activity-log', component: ActivityLogView, meta: { title: '操作日志' } },
    { path: '/assets/:assetId', name: 'asset-detail', component: AssetDetailView, meta: { title: '资产详情' } },
    { path: '/datasets', name: 'datasets', component: AssetsView, meta: { assetType: 'dataset', title: '数据集目录' } },
    { path: '/literature', name: 'literature', component: AssetsView, meta: { assetType: 'literature', title: '文献目录' } },
    { path: '/projects', name: 'projects', component: AssetsView, meta: { assetType: 'project', title: '项目目录' } },
    { path: '/models', name: 'models', component: AssetsView, meta: { assetType: 'model', title: '模型目录' } },
    { path: '/:pathMatch(.*)*', name: 'not-found', component: NotFoundView, meta: { title: '页面未找到' } },
  ],
})

router.afterEach((to) => {
  useBranding().setPageTitle(String(to.meta.title ?? '数据管理'))
})

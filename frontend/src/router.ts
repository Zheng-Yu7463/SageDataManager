import { createRouter, createWebHistory } from 'vue-router'

import AssetsView from '@/views/AssetsView.vue'
import ArchiveHealthView from '@/views/ArchiveHealthView.vue'
import PendingFilesView from '@/views/PendingFilesView.vue'
import ImportAssetsView from '@/views/ImportAssetsView.vue'
import AssetDetailView from '@/views/AssetDetailView.vue'
import ArchivedAssetsView from '@/views/ArchivedAssetsView.vue'
import DashboardView from '@/views/DashboardView.vue'
import SearchView from '@/views/SearchView.vue'
import SettingsView from '@/views/SettingsView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: DashboardView },
    { path: '/search', name: 'search', component: SearchView },
    { path: '/papers', name: 'papers', component: AssetsView, meta: { assetType: 'paper' } },
    { path: '/settings', name: 'settings', component: SettingsView },
    { path: '/archive-health', name: 'archive-health', component: ArchiveHealthView },
    { path: '/archived-assets', name: 'archived-assets', component: ArchivedAssetsView },
    { path: '/import-assets', name: 'import-assets', component: ImportAssetsView },
    { path: '/unclaimed-files', name: 'unclaimed-files', component: PendingFilesView },
    { path: '/assets/:assetId', name: 'asset-detail', component: AssetDetailView },
    { path: '/datasets', name: 'datasets', component: AssetsView, meta: { assetType: 'dataset' } },
    { path: '/literature', name: 'literature', component: AssetsView, meta: { assetType: 'literature' } },
    { path: '/projects', name: 'projects', component: AssetsView, meta: { assetType: 'project' } },
    { path: '/models', name: 'models', component: AssetsView, meta: { assetType: 'model' } },
  ],
})

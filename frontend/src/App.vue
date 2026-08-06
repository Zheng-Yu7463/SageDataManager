<script setup lang="ts">
import { NConfigProvider, NDialogProvider, NMessageProvider, zhCN, dateZhCN } from 'naive-ui'

import AppShell from '@/components/AppShell.vue'

const themeOverrides = {
  common: {
    primaryColor: '#2e7351',
    primaryColorHover: '#245f42',
    primaryColorPressed: '#1d5037',
    borderRadius: '8px',
    fontFamily: '"Avenir Next", "PingFang SC", sans-serif',
  },
}
</script>

<template>
  <NConfigProvider :locale="zhCN" :date-locale="dateZhCN" :theme-overrides="themeOverrides">
    <NDialogProvider>
      <NMessageProvider>
        <AppShell />
      </NMessageProvider>
    </NDialogProvider>
  </NConfigProvider>
</template>


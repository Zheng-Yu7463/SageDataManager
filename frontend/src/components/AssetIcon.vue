<script setup lang="ts">
import { BookOpen, Box, Database, FileText, FolderKanban } from '@lucide/vue'

import type { AssetType } from '@/types'

defineProps<{ type: AssetType; size?: number }>()

const icons = {
  paper: FileText,
  dataset: Database,
  literature: BookOpen,
  project: FolderKanban,
  model: Box,
}
</script>

<template>
  <component :is="icons[type]" :size="size ?? 20" :stroke-width="1.8" />
</template>

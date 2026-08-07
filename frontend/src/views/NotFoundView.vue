<script setup lang="ts">
import { ArrowLeft, Compass } from '@lucide/vue'
</script>

<template>
  <div class="page not-found-page">
    <section class="not-found-card">
      <span class="not-found-icon"><Compass :size="30" /></span>
      <p class="eyebrow">SAGE NAVIGATION</p>
      <h1>未找到这个页面</h1>
      <p>它可能已被移动、归档，或链接地址有误。</p>
      <RouterLink class="button button--primary" to="/">
        <ArrowLeft :size="16" /> 返回实验室总览
      </RouterLink>
    </section>
  </div>
</template>

<style scoped>
.not-found-page { display: grid; min-height: 60vh; place-items: center; }
.not-found-card { display: grid; width: min(100%, 480px); padding: 42px; text-align: center; background: #fcfdf9; border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 12px 28px rgba(29, 47, 34, .07); justify-items: center; gap: 11px; }
.not-found-icon { display: grid; width: 62px; height: 62px; color: var(--sage); place-items: center; background: #e7f0e7; border-radius: 50%; }
.not-found-card h1 { margin: 0; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 30px; font-weight: 500; }.not-found-card p:not(.eyebrow) { margin: 0 0 9px; color: var(--muted); font-size: 13px; }
</style>

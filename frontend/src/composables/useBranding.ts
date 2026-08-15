import { computed, reactive, readonly } from 'vue'

import { getInstanceBranding } from '@/api/client'
import type { InstanceBranding } from '@/types'

const defaultBranding: InstanceBranding = {
  product_name: 'SAGE',
  product_subtitle: 'RESEARCH ARCHIVE',
  organization_name: 'SAGE Lab',
  slogan: '科学 · 数据 · 成长 · 卓越',
  slogan_secondary: 'Science · Archive · Growth · Excellence',
  primary_color: '#2E7351',
  logo_url: null,
  revision: 'default',
}

const branding = reactive<InstanceBranding>({ ...defaultBranding })
let currentPageTitle = '数据管理'

function mixWithWhite(hex: string, ratio: number) {
  const channels = [1, 3, 5].map((index) => Number.parseInt(hex.slice(index, index + 2), 16))
  return `rgb(${channels.map((channel) => Math.round(channel + (255 - channel) * ratio)).join(' ')})`
}

function mixWithBlack(hex: string, ratio: number) {
  const channels = [1, 3, 5].map((index) => Number.parseInt(hex.slice(index, index + 2), 16))
  return `rgb(${channels.map((channel) => Math.round(channel * (1 - ratio))).join(' ')})`
}

function applyBranding(next: InstanceBranding) {
  Object.assign(branding, next)
  document.title = `${currentPageTitle} · ${next.product_name}`
  const root = document.documentElement
  root.style.setProperty('--sage', next.primary_color)
  root.style.setProperty('--sage-dark', mixWithBlack(next.primary_color, 0.24))
  root.style.setProperty('--sage-soft', mixWithWhite(next.primary_color, 0.84))

  let favicon = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
  if (next.logo_url) {
    if (!favicon) {
      favicon = document.createElement('link')
      favicon.rel = 'icon'
      document.head.append(favicon)
    }
    favicon.href = next.logo_url
  } else if (favicon) {
    favicon.remove()
  }
}

async function loadBranding() {
  try {
    applyBranding(await getInstanceBranding())
  } catch {
    applyBranding(defaultBranding)
  }
}

function pageEyebrow(section: string) {
  return `${branding.product_name} ${section}`
}

function setPageTitle(title: string) {
  currentPageTitle = title
  document.title = `${title} · ${branding.product_name}`
}

export function useBranding() {
  return {
    branding: readonly(branding),
    brandTitle: computed(() => `${branding.product_name} ${branding.product_subtitle}`),
    applyBranding,
    loadBranding,
    pageEyebrow,
    setPageTitle,
  }
}

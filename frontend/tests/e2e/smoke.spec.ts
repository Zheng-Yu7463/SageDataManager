import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'

function configuredPassword() {
  const content = readFileSync('../.env', 'utf8')
  const line = content
    .split(/\r?\n/)
    .find((item) => item.startsWith('SAGE_FIXED_ACCOUNT_PASSWORD='))
  if (!line) throw new Error('SAGE_FIXED_ACCOUNT_PASSWORD is required for browser tests.')
  return line.slice('SAGE_FIXED_ACCOUNT_PASSWORD='.length)
}

async function signIn(page: Page) {
  await page.goto('/')
  await page.getByLabel('账号').fill('zhengyu')
  await page.getByLabel('密码').fill(configuredPassword())
  await page.getByRole('button', { name: '进入归档系统' }).click()
  await expect(page.getByRole('heading', { name: '实验室科研资产总览' })).toBeVisible()
}

test('管理员可登录、筛选目录并生成上传指令', async ({ page }) => {
  await signIn(page)
  await page.getByRole('link', { name: /数据集/ }).click()
  await page.getByRole('button', { name: /筛选条件/ }).click()
  await page.getByLabel('数据状态').selectOption('present')
  await expect(page.getByText('ClimateBench v2.1 数据集')).toBeVisible()
  await page.getByTitle('获取此资产的 SCP 上传指令').click()
  await page.getByLabel('本机待上传文件或目录').fill('/tmp/visual-e2e.csv')
  await page.getByRole('button', { name: '生成 SCP 命令' }).click()
  await expect(page.getByText('上传指令已生成')).toBeVisible()
})

test('总览与目录保留视觉基线', async ({ page }) => {
  await signIn(page)
  await expect(page).toHaveScreenshot('dashboard.png', {
    fullPage: true,
    maxDiffPixelRatio: 0.02,
  })
  await page.getByRole('link', { name: /数据集/ }).click()
  await expect(page).toHaveScreenshot('datasets.png', {
    fullPage: true,
    maxDiffPixelRatio: 0.02,
  })
})

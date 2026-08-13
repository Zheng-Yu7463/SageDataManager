import { expect, test, type Page } from '@playwright/test'
import { existsSync, readFileSync } from 'node:fs'

function configuredPassword() {
  if (process.env.SAGE_FIXED_ACCOUNT_PASSWORD) return process.env.SAGE_FIXED_ACCOUNT_PASSWORD
  if (!existsSync('../.env')) {
    throw new Error('SAGE_FIXED_ACCOUNT_PASSWORD is required for browser tests.')
  }
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
  await page.getByRole('link', { name: '数据集 Datasets', exact: true }).click()
  await page.getByRole('button', { name: /筛选条件/ }).click()
  await page.getByLabel('数据状态').selectOption('present')
  await expect(page.getByText('ClimateBench v2.1 数据集')).toBeVisible()
  await page.getByTitle('获取此资产的 SCP 上传指令').click()
  await page.getByLabel('本机待上传文件或目录').fill('/tmp/visual-e2e.csv')
  await page.getByRole('button', { name: '生成 SCP 命令' }).click()
  await expect(page.getByText('上传指令已生成')).toBeVisible()
})

test('资产详情提供可折叠的文件浏览器', async ({ page }) => {
  await signIn(page)
  await page.getByRole('link', { name: '数据集 Datasets', exact: true }).click()
  const climateBench = page.locator('.catalogue-card').filter({ hasText: 'ClimateBench v2.1 数据集' })
  await climateBench.getByRole('link', { name: /查看详情/ }).click()
  await expect(page.getByRole('heading', { name: '文件浏览' })).toBeVisible()
  await expect(page.getByText('README.md', { exact: true })).toBeVisible()
})

test('总览与目录保留视觉基线', async ({ page }) => {
  await signIn(page)
  await expect(page).toHaveScreenshot('dashboard.png', {
    fullPage: true,
    maxDiffPixelRatio: 0.02,
  })
  await page.getByRole('link', { name: '数据集 Datasets', exact: true }).click()
  await expect(page).toHaveScreenshot('datasets.png', {
    fullPage: true,
    maxDiffPixelRatio: 0.02,
  })
})

test('账户菜单明确区分资料与退出操作', async ({ page }) => {
  await signIn(page)
  const accountMenu = page.getByRole('button', { name: /郑宇/ })
  await accountMenu.click()
  await expect(page.getByRole('menuitem', { name: '退出登录' })).toBeVisible()
  await expect(page.getByText('zhengyu@sage.lab')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('menuitem', { name: '退出登录' })).toBeHidden()
  await expect(page.getByRole('heading', { name: '实验室科研资产总览' })).toBeVisible()
})

test('运行中的失效会话会立即返回登录页', async ({ page }) => {
  await signIn(page)
  await page.evaluate(() => window.localStorage.setItem('sage-session-token', 'invalid-session'))
  await page.goto('/papers')

  await expect(page.getByRole('button', { name: '进入归档系统' })).toBeVisible()
  await expect(page.locator('.app-shell')).toBeHidden()
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem('sage-session-token'))).toBeNull()
})

test('登录失败不会把匿名请求当作会话失效', async ({ page }) => {
  await page.goto('/')
  await page.evaluate(() => window.localStorage.setItem('sage-session-token', 'unrelated-token'))
  await page.getByLabel('账号').fill('zhengyu')
  await page.getByLabel('密码').fill('wrong-password')
  await page.getByRole('button', { name: '进入归档系统' }).click()

  await expect(page.getByText('账号或密码错误。')).toBeVisible()
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem('sage-session-token'))).toBe('unrelated-token')
})

test('弹窗锁定背景滚动并支持 Esc 关闭', async ({ page }) => {
  await signIn(page)
  await page.getByRole('link', { name: '论文 Papers', exact: true }).click()
  await page.getByRole('button', { name: '登记论文' }).click()
  await expect(page.getByRole('dialog', { name: '登记论文' })).toBeVisible()
  await expect(page.getByLabel('标题')).toBeFocused()
  await expect(page.locator('body')).toHaveCSS('overflow', 'hidden')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: '登记论文' })).toBeHidden()
  await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden')
  await expect(page.getByRole('button', { name: '登记论文' })).toBeFocused()
})

test('页面导航同步浏览器标题与滚动位置', async ({ page }) => {
  await signIn(page)
  await page.getByRole('link', { name: '论文 Papers', exact: true }).click()
  await expect(page).toHaveTitle('论文目录 · SAGE')
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
  await page.getByRole('link', { name: '系统设置' }).click()
  await expect(page).toHaveTitle('系统设置 · SAGE')
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0)
})

test('品牌设置保存后立即更新全站标识', async ({ page }) => {
  await signIn(page)
  await page.getByRole('link', { name: '系统设置' }).click()
  await page.getByLabel('产品名称').fill('Atlas')
  await page.getByLabel('产品副标题').fill('DATA MANAGER')
  await page.getByLabel('组织名称').fill('Atlas Institute')
  await page.getByLabel('主标语').fill('研究 · 连接 · 积累')
  await page.getByLabel('辅助标语').fill('Research · Connect · Preserve')
  await page.getByLabel('品牌主色色值').fill('#245B78')
  await page.getByRole('button', { name: '保存品牌设置' }).click()
  await expect(page.getByText('品牌设置已应用')).toBeVisible()
  await expect(page.locator('.brand').getByText('Atlas', { exact: true })).toBeVisible()
  await expect(page).toHaveTitle('系统设置 · Atlas')
})

test('目录筛选与视图状态可通过 URL 恢复', async ({ page }) => {
  await signIn(page)
  await page.getByRole('link', { name: '论文 Papers', exact: true }).click()
  await page.getByRole('button', { name: /筛选条件/ }).click()
  await page.getByLabel('收录会议').selectOption('ICLR')
  await page.getByRole('button', { name: '卡片视图' }).click()
  await expect(page).toHaveURL(/venue=ICLR/)
  await expect(page).toHaveURL(/view=grid/)
  await page.reload()
  await expect(page.getByLabel('收录会议')).toHaveValue('ICLR')
  await expect(page.getByRole('button', { name: '卡片视图' })).toHaveAttribute('aria-pressed', 'true')
})

test('目录筛选浮层支持键盘和外部关闭', async ({ page }) => {
  await signIn(page)
  await page.goto('/papers')
  const trigger = page.getByRole('button', { name: /筛选条件/ })

  await trigger.click()
  await page.getByLabel('收录会议').selectOption('ICLR')
  await expect(page.getByLabel('收录会议')).toBeVisible()
  await expect(page).toHaveURL(/venue=ICLR/)

  await page.keyboard.press('Escape')
  await expect(page.getByLabel('收录会议')).toBeHidden()
  await expect(trigger).toBeFocused()

  await trigger.click()
  await page.locator('.assets-heading-copy').click()
  await expect(page.getByLabel('收录会议')).toBeHidden()

  const layout = await page.evaluate(() => ({
    viewportWidth: document.documentElement.clientWidth,
    documentWidth: document.documentElement.scrollWidth,
  }))
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth)
})

test('待认领文件必须搜索并明确选择目标资产', async ({ page }) => {
  await signIn(page)
  await page.route('**/api/archive/unclaimed', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify([{
        id: '11111111-1111-1111-1111-111111111111',
        relative_path: 'incoming/unassigned.pdf',
        file_name: 'unassigned.pdf',
        file_kind: 'document',
        mime_type: 'application/pdf',
        file_size: 2048,
        modified_at: null,
        first_seen_at: '2026-08-13T04:00:00Z',
        last_seen_at: '2026-08-13T04:00:00Z',
      }]),
    })
  })
  await page.route('**/api/assets/choices?*', async (route) => {
    const query = new URL(route.request().url()).searchParams.get('query')
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(query === 'target-149' ? [{
        id: '22222222-2222-2222-2222-222222222222',
        type: 'dataset',
        slug: 'target-149',
        title: '第 150 项资产',
      }] : []),
    })
  })

  await page.goto('/unclaimed-files')
  await page.getByRole('button', { name: '认领' }).click()
  const confirm = page.getByRole('button', { name: '确认认领' })
  await expect(confirm).toBeDisabled()

  await page.getByLabel('归属资产').fill('target-149')
  await expect(page.getByRole('option', { name: /第 150 项资产/ })).toBeVisible()
  await page.getByRole('option', { name: /第 150 项资产/ }).click()
  await expect(confirm).toBeEnabled()
  await expect(page.getByText('target-149')).toBeVisible()

  await page.getByLabel('归属资产').fill('different-target')
  await expect(confirm).toBeDisabled()

  const layout = await page.evaluate(() => ({
    viewportWidth: document.documentElement.clientWidth,
    documentWidth: document.documentElement.scrollWidth,
  }))
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth)
})

test('操作日志使用服务端活动标签和筛选项', async ({ page }) => {
  await signIn(page)
  await page.goto('/activity-log')
  const filter = page.getByLabel('操作类型')

  await expect(filter.locator('option[value="file_accessed"]')).toHaveCount(0)
  await expect(page.locator('.activity-table')).not.toContainText(/downloaded_file|previewed_file|updated_branding/)
})

test('详情页返回到原目录状态', async ({ page }) => {
  await signIn(page)
  await page.goto('/papers?venue=ICLR&view=grid')
  await page.getByRole('link', { name: /查看详情/ }).first().click()
  await expect(page).toHaveURL(/returnTo=/)
  await page.getByRole('button', { name: '返回目录' }).click()
  await expect(page).toHaveURL(/\/papers\?venue=ICLR&view=grid/)
  await expect(page.getByLabel('收录会议')).toHaveValue('ICLR')
})

test('论文登记在专属字段完整后才允许提交', async ({ page }) => {
  await signIn(page)
  await page.getByRole('link', { name: '论文 Papers', exact: true }).click()
  await page.getByRole('button', { name: '登记论文' }).click()
  await page.getByLabel('标题').fill('测试论文')
  await page.getByLabel('资产标识（slug）').fill('test-paper')
  const submit = page.getByRole('button', { name: '确认登记' })
  await expect(submit).toBeDisabled()
  await page.getByLabel('会议').fill('ICLR')
  await page.getByLabel('会议类别').fill('Conference Poster')
  await page.getByLabel('作者（逗号分隔）').fill('Ada Lovelace')
  await page.getByLabel('官方来源标识').fill('test-paper-2026')
  await page.getByLabel('官方页面 URL').fill('https://example.com/paper')
  await page.getByLabel('官方 PDF URL').fill('https://example.com/paper.pdf')
  await expect(submit).toBeEnabled()
})

test('论文目录和详情提供统一 BibTeX 引用', async ({ page }) => {
  await signIn(page)
  await page.getByRole('link', { name: '论文 Papers', exact: true }).click()
  await expect(page.getByRole('button', { name: '导出 BibTeX' })).toBeEnabled()
  await page.getByRole('link', { name: /查看详情/ }).first().click()
  await expect(page.getByRole('heading', { name: '论文引用' })).toBeVisible()
  await expect(page.locator('.paper-citation pre')).toContainText('@inproceedings{')
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载 .bib' }).click()
  await expect((await download).suggestedFilename()).toMatch(/\.bib$/)
})

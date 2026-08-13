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

async function navigateTo(page: Page, linkName: string | RegExp) {
  const menuButton = page.getByRole('button', { name: '打开导航' })
  if (await menuButton.isVisible()) await menuButton.click()
  await page.getByRole('link', { name: linkName, exact: typeof linkName === 'string' }).click()
}

test('管理员可登录并进入安全上传闭环', async ({ page }) => {
  await signIn(page)
  await page.goto('/literature?view=grid')
  await expect(page.getByRole('heading', { name: '文献目录' })).toBeVisible()
  await page.getByRole('button', { name: '上传文件' }).first().click()
  await page.getByLabel('本机待上传路径').fill('/tmp/visual-e2e.csv')
  await page.getByRole('button', { name: '生成上传命令' }).click()
  await expect(page.getByText('终端上传命令')).toBeVisible()
  await expect(page.getByRole('button', { name: '检测并入库' })).toBeVisible()
})

test('资产详情提供可折叠的文件浏览器', async ({ page }) => {
  await signIn(page)
  await page.goto('/literature?view=grid')
  const assetWithFiles = page.locator('.catalogue-card').filter({ hasText: /已有数据/ }).first()
  await assetWithFiles.getByRole('link', { name: /查看详情/ }).click()
  await expect(page.getByRole('heading', { name: '文件浏览' })).toBeVisible()
  await expect(page.getByRole('button', { name: '下载文件' }).first()).toBeVisible()
})

test('总览与目录保持稳定布局', async ({ page }) => {
  await signIn(page)
  await expect(page.getByRole('region', { name: '资产分类统计' })).toBeVisible()
  await page.goto('/literature?view=grid')
  await expect(page.getByRole('heading', { name: '文献目录' })).toBeVisible()
  const layout = await page.evaluate(() => ({
    viewportWidth: document.documentElement.clientWidth,
    documentWidth: document.documentElement.scrollWidth,
    cards: document.querySelectorAll('.catalogue-card').length,
  }))
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth)
  expect(layout.cards).toBeGreaterThan(0)
})

test('账户菜单明确区分资料与退出操作', async ({ page }) => {
  await signIn(page)
  const accountMenu = page.getByRole('button', { name: /账户菜单/ })
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
  await navigateTo(page, '论文 Papers')
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
  await navigateTo(page, '论文 Papers')
  await expect(page).toHaveTitle('论文目录 · SAGE')
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
  await navigateTo(page, '系统设置')
  await expect(page).toHaveTitle('系统设置 · SAGE')
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0)
})

test('品牌设置保存后立即更新全站标识', async ({ page }) => {
  await signIn(page)
  await page.route('**/api/settings/branding', async (route) => {
    if (route.request().method() !== 'PATCH') {
      await route.continue()
      return
    }
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        product_name: 'Atlas',
        product_subtitle: 'DATA MANAGER',
        organization_name: 'Atlas Institute',
        slogan: '研究 · 连接 · 积累',
        slogan_secondary: 'Research · Connect · Preserve',
        primary_color: '#245B78',
        logo_url: null,
      }),
    })
  })
  await navigateTo(page, '系统设置')
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

test('AI 访问令牌保护一次性明文并归档失效记录', async ({ page }) => {
  await signIn(page)
  const createdToken = {
    id: '33333333-3333-3333-3333-333333333333',
    name: '自动化验收',
    token_prefix: 'sdm_pat_audit12345678',
    token: 'sdm_pat_audit12345678_one_time_secret',
    scopes: ['assets:read', 'metadata:write'],
    created_at: '2026-08-13T12:00:00Z',
    expires_at: '2026-11-11T12:00:00Z',
    last_used_at: null,
    revoked_at: null,
  }
  await page.route('**/api/auth/access-tokens', async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(createdToken) })
      return
    }
    await route.fulfill({ contentType: 'application/json', body: '[]' })
  })
  await page.route(`**/api/auth/access-tokens/${createdToken.id}`, async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ ...createdToken, token: undefined, revoked_at: '2026-08-13T12:05:00Z' }),
    })
  })

  await page.goto('/settings')
  await page.getByRole('button', { name: '新建令牌' }).click()
  const createDialog = page.getByRole('dialog', { name: '创建 AI 访问令牌' })
  await createDialog.getByLabel('令牌名称').fill(createdToken.name)
  await createDialog.getByRole('button', { name: '创建令牌' }).click()

  const createdDialog = page.getByRole('dialog', { name: '令牌已创建' })
  await expect(createdDialog).toContainText(createdToken.token)
  await page.keyboard.press('Escape')
  await expect(createdDialog).toBeVisible()
  await expect(createdDialog.getByRole('button', { name: '关闭' })).toHaveCount(0)

  await createdDialog.getByRole('button', { name: '我已安全保存' }).click()
  await expect(page.getByText(createdToken.token, { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '撤销令牌' }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: '确认撤销' }).click()

  const history = page.getByRole('button', { name: /历史令牌/ })
  await expect(history).toHaveAttribute('aria-expanded', 'false')
  await history.click()
  await expect(page.locator('.token-list--history')).toContainText('自动化验收')
  await expect(page.locator('.token-list--history')).toContainText('已撤销')
})

test('目录筛选与视图状态可通过 URL 恢复', async ({ page }) => {
  await signIn(page)
  await navigateTo(page, '文献 Literature')
  await page.getByRole('button', { name: /筛选条件/ }).click()
  await page.getByLabel('发表来源').selectOption('ICLR')
  await page.getByRole('button', { name: '卡片视图' }).click()
  await expect(page).toHaveURL(/venue=ICLR/)
  await expect(page).toHaveURL(/view=grid/)
  await page.reload()
  await page.getByRole('button', { name: /筛选条件/ }).click()
  await expect(page.getByLabel('发表来源')).toHaveValue('ICLR')
  await expect(page.getByRole('button', { name: '卡片视图' })).toHaveAttribute('aria-pressed', 'true')
})

test('目录筛选浮层支持键盘和外部关闭', async ({ page }) => {
  await signIn(page)
  await page.goto('/literature')
  const trigger = page.getByRole('button', { name: /筛选条件/ })

  await trigger.click()
  await page.getByLabel('发表来源').selectOption('ICLR')
  await expect(page.getByLabel('发表来源')).toBeVisible()
  await expect(page).toHaveURL(/venue=ICLR/)

  await page.keyboard.press('Escape')
  await expect(page.getByLabel('发表来源')).toBeHidden()
  await expect(trigger).toBeFocused()

  await trigger.click()
  await page.locator('.assets-heading-copy').click()
  await expect(page.getByLabel('发表来源')).toBeHidden()

  const layout = await page.evaluate(() => ({
    viewportWidth: document.documentElement.clientWidth,
    documentWidth: document.documentElement.scrollWidth,
  }))
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth)
})

test('连续搜索只显示最新请求的状态和结果', async ({ page }) => {
  await signIn(page)
  let releaseLatestSearch: (() => void) | undefined
  const latestSearchReleased = new Promise<void>((resolve) => { releaseLatestSearch = resolve })
  let markLatestSearchStarted: (() => void) | undefined
  const latestSearchStarted = new Promise<void>((resolve) => { markLatestSearchStarted = resolve })
  await page.route('**/api/assets?*', async (route) => {
    const query = new URL(route.request().url()).searchParams.get('query')
    if (query === 'latest') {
      markLatestSearchStarted?.()
      await latestSearchReleased
    }
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        items: query === 'latest' ? [{
          id: '55555555-5555-5555-5555-555555555555',
          type: 'project',
          slug: 'latest-result',
          title: '最新搜索结果',
          summary: '只允许当前请求更新页面',
          status: 'active',
          visibility: 'lab',
          owner: { id: '66666666-6666-6666-6666-666666666666', name: '测试用户', avatar_url: null },
          details: {},
          tags: [],
          current_version: null,
          total_size: 0,
          file_count: 0,
          upload_directories: [],
          default_upload_directory: 'documents',
          updated_at: '2026-08-13T06:00:00Z',
        }] : [],
        total: query === 'latest' ? 1 : 0,
        page: 1,
        page_size: 20,
        publication_facets: null,
      }),
    })
  })

  await page.goto('/search?q=initial')
  await expect(page.getByText('没有匹配的资产')).toBeVisible()
  await page.getByPlaceholder('输入标题、摘要或关键词').fill('latest')
  await expect(page.locator('.search-summary')).toContainText('与“initial”相关')
  await page.getByRole('button', { name: '检索目录' }).click()
  await latestSearchStarted
  await expect(page.locator('.search-summary .tiny-spinner')).toBeVisible()
  await expect(page.getByText('没有匹配的资产')).toBeHidden()

  releaseLatestSearch?.()
  await expect(page.getByText('最新搜索结果')).toBeVisible()
  await expect(page.locator('.search-summary .tiny-spinner')).toBeHidden()
})

test('搜索页归一化非法和越界页码', async ({ page }) => {
  await signIn(page)
  await page.route('**/api/assets?*', async (route) => {
    const url = new URL(route.request().url())
    const requestedPage = Number(url.searchParams.get('page'))
    const item = requestedPage === 2 ? [{
      id: '77777777-7777-7777-7777-777777777777',
      type: 'dataset',
      slug: 'last-page-result',
      title: '最后一页结果',
      summary: '用于验证页码归一化',
      status: 'active',
      visibility: 'lab',
      owner: { id: '88888888-8888-8888-8888-888888888888', name: '测试用户', avatar_url: null },
      details: {},
      tags: [],
      current_version: null,
      total_size: 0,
      file_count: 0,
      upload_directories: [],
      default_upload_directory: 'raw',
      updated_at: '2026-08-13T06:00:00Z',
    }] : []
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ items: item, total: 21, page: requestedPage, page_size: 20, publication_facets: null }),
    })
  })

  await page.goto('/search?q=paged&page=0')
  await expect(page).toHaveURL(/q=paged(?!.*page=0)/)
  await page.goto('/search?q=paged&page=999')
  await expect(page).toHaveURL(/q=paged&page=2/)
  await expect(page.getByText('最后一页结果')).toBeVisible()
})

test('批量导入文件选择器支持页面声明的三种格式', async ({ page }) => {
  await signIn(page)
  await page.goto('/import-assets')
  const acceptedTypes = await page.locator('.import-file-picker input').getAttribute('accept')

  expect(acceptedTypes).toContain('.json')
  expect(acceptedTypes).toContain('.csv')
  expect(acceptedTypes).toContain('.yaml')
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
  await page.goto('/literature?venue=ICLR&view=grid')
  await page.getByRole('link', { name: /查看详情/ }).first().click()
  await expect(page).toHaveURL(/returnTo=/)
  await page.getByRole('button', { name: '返回目录' }).click()
  await expect(page).toHaveURL(/\/literature\?venue=ICLR&view=grid/)
  await expect(page.getByRole('button', { name: /筛选条件 1/ })).toBeVisible()
  await expect(page.getByRole('button', { name: '卡片视图' })).toHaveAttribute('aria-pressed', 'true')
})

test('论文登记在专属字段完整后才允许提交', async ({ page }) => {
  await signIn(page)
  await navigateTo(page, '论文 Papers')
  await page.getByRole('button', { name: '登记论文' }).click()
  await page.getByLabel('标题').fill('测试论文')
  await page.getByLabel('资产标识（slug）').fill('test-paper')
  const submit = page.getByRole('button', { name: '确认登记' })
  await expect(submit).toBeDisabled()
  await page.getByLabel('会议', { exact: true }).fill('ICLR')
  await page.getByLabel('会议类别').fill('Conference Poster')
  await page.getByLabel('作者（逗号分隔）').fill('Ada Lovelace')
  await page.getByLabel('官方来源标识').fill('test-paper-2026')
  await page.getByLabel('官方页面 URL').fill('https://example.com/paper')
  await page.getByLabel('官方 PDF URL').fill('https://example.com/paper.pdf')
  await expect(submit).toBeEnabled()
})

test('文献登记提交完整期刊引用元数据', async ({ page }) => {
  await signIn(page)
  let requestBody: Record<string, unknown> | undefined
  await page.route('**/api/assets', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue()
      return
    }
    requestBody = route.request().postDataJSON() as Record<string, unknown>
    await route.fulfill({ status: 201, contentType: 'application/json', body: '{}' })
  })

  await page.goto('/literature')
  await page.getByRole('button', { name: '登记文献' }).click()
  await page.getByLabel('标题').fill('测试期刊文献')
  await page.getByLabel('资产标识（slug）').fill('test-journal-literature')
  const submit = page.getByRole('button', { name: '确认登记' })
  await expect(submit).toBeDisabled()
  await page.getByLabel('来源或期刊').fill('Nature Communications')
  await page.getByLabel('文献类别').fill('Journal Article')
  await page.getByLabel('作者（逗号分隔）').fill('Ada Lovelace')
  await page.getByLabel('官方来源标识').fill('doi:10.1000/test')
  await page.getByLabel('官方页面 URL').fill('https://example.com/article')
  await page.getByLabel('官方 PDF URL').fill('https://example.com/article.pdf')
  await expect(submit).toBeDisabled()
  await page.getByLabel('期刊名称').fill('Nature Communications')
  await expect(submit).toBeEnabled()
  await submit.click()

  await expect.poll(() => requestBody).toBeDefined()
  expect(requestBody?.type).toBe('literature')
  expect(requestBody?.details).toMatchObject({
    entry_type: 'article',
    journal: 'Nature Communications',
    venue: 'Nature Communications',
  })
})

test('关联资产通过服务端搜索覆盖完整目录', async ({ page }) => {
  await signIn(page)
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

  await page.goto('/literature?view=grid')
  await page.getByRole('link', { name: /查看详情/ }).first().click()
  await page.getByRole('button', { name: '添加关联' }).click()
  const confirm = page.getByRole('button', { name: '建立关联' })
  await expect(confirm).toBeDisabled()
  await page.getByLabel('关联到').fill('target-149')
  await expect(page.getByRole('option', { name: /第 150 项资产/ })).toBeVisible()
  await page.getByRole('option', { name: /第 150 项资产/ }).click()
  await expect(confirm).toBeEnabled()
  await page.getByLabel('关联到').fill('different-target')
  await expect(confirm).toBeDisabled()
})

test('首页最近归档入口指向最新资产所属目录', async ({ page }) => {
  await signIn(page)
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 1, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        popular_tags: [],
        recent_activities: [],
        recent_assets: [{
          id: '33333333-3333-3333-3333-333333333333',
          type: 'literature',
          slug: 'latest-literature',
          title: '最新外部文献',
          summary: '用于验证目录入口',
          status: 'published',
          visibility: 'lab',
          owner: { id: '44444444-4444-4444-4444-444444444444', name: '测试用户', avatar_url: null },
          details: {},
          tags: [],
          current_version: null,
          total_size: 0,
          file_count: 0,
          upload_directories: [],
          default_upload_directory: 'source',
          updated_at: '2026-08-13T06:00:00Z',
        }],
      }),
    })
  })

  await page.goto('/')
  await expect(page.getByRole('link', { name: '查看文献目录' })).toHaveAttribute('href', '/literature')
})

test('文献目录和详情提供统一 BibTeX 引用', async ({ page }) => {
  await signIn(page)
  await navigateTo(page, '文献 Literature')
  await expect(page.getByRole('button', { name: '导出 BibTeX' })).toBeEnabled()
  await page.getByRole('link', { name: /查看详情/ }).first().click()
  await expect(page.getByRole('heading', { name: '出版物引用' })).toBeVisible()
  await expect(page.locator('.publication-citation pre')).toContainText(/^@(article|inproceedings|misc)\{/)
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载 .bib' }).click()
  await expect((await download).suggestedFilename()).toMatch(/\.bib$/)
})

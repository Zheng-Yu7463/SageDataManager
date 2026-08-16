import { expect, test, type Page } from '@playwright/test'
import { existsSync, readFileSync } from 'node:fs'

function localFixedPassword() {
  if (process.env.SAGE_FIXED_ACCOUNT_PASSWORD) return process.env.SAGE_FIXED_ACCOUNT_PASSWORD
  if (!existsSync('../.env')) return null
  const line = readFileSync('../.env', 'utf8')
    .split(/\r?\n/)
    .find((item) => item.startsWith('SAGE_FIXED_ACCOUNT_PASSWORD='))
  return line?.slice('SAGE_FIXED_ACCOUNT_PASSWORD='.length) || null
}

function liveCredentials() {
  if (process.env.SAGE_E2E_SKIP_LIVE === '1') return null
  const username = process.env.SAGE_E2E_USERNAME
  const password = process.env.SAGE_E2E_PASSWORD
  if (username && password) return { username, password }

  const baseUrl = new URL(process.env.SAGE_E2E_BASE_URL ?? 'http://127.0.0.1:8080')
  if (!['127.0.0.1', 'localhost'].includes(baseUrl.hostname)) return null
  const localPassword = localFixedPassword()
  return localPassword ? { username: username ?? 'zhengyu', password: localPassword } : null
}

function systemUpdateStatus(overrides: Record<string, unknown> = {}) {
  return {
    enabled: false,
    state: 'unavailable',
    phase: null,
    message: '服务器尚未安装更新代理。',
    branch: 'main',
    current_commit: null,
    latest_commit: null,
    checked_at: null,
    update_available: false,
    behind_count: 0,
    ahead_count: 0,
    worktree_clean: null,
    remote_url: null,
    commits: [],
    started_at: null,
    completed_at: null,
    error: null,
    backup_path: null,
    backup_in_progress: false,
    last_backup_at: null,
    last_backup_path: null,
    last_backup_error: null,
    next_backup_at: null,
    scheduled_backup_interval_seconds: 86400,
    operation_id: null,
    agent_restart_required: false,
    installer_restart_required: false,
    logs: [],
    ...overrides,
  }
}

async function signIn(page: Page) {
  const credentials = liveCredentials()
  test.skip(
    !credentials,
    'Set SAGE_E2E_USERNAME and SAGE_E2E_PASSWORD to run live tests against a remote instance.',
  )
  await page.goto('/')
  await page.getByLabel('账号').fill(credentials!.username)
  await page.getByLabel('密码').fill(credentials!.password)
  await page.getByRole('button', { name: '进入归档系统' }).click()
  await expect(page.getByRole('heading', { name: '实验室科研资产总览' })).toBeVisible()
}

async function signInWithMockAccount(page: Page) {
  const account = {
    id: '90909090-9090-9090-9090-909090909090',
    username: 'testadmin',
    name: '测试管理员',
    email: 'test-admin@sage.test',
    role: 'admin',
    upload_username: 'testadmin',
    is_active: true,
    is_instance_owner: true,
    is_registered: true,
  }
  await page.route('**/api/auth/setup-status', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ initialized: true, authentication_ready: true }),
    })
  })
  await page.route('**/api/auth/login', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ ...account, session_token: 'mock-session-token' }),
    })
  })
  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(account) })
  })
  await page.route('**/api/settings/system-update', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(systemUpdateStatus()),
    })
  })
  await page.goto('/')
  await page.getByLabel('账号').fill('testadmin')
  await page.getByLabel('密码').fill('test-password')
  await page.getByRole('button', { name: '进入归档系统' }).click()
  await expect(page.getByRole('button', { name: '账户菜单：测试管理员' })).toBeVisible()
}

async function mockRejectedLogin(page: Page) {
  await page.route('**/api/auth/setup-status', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ initialized: true, authentication_ready: true }),
    })
  })
  await page.route('**/api/auth/login', async (route) => {
    await route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({ detail: '账号或密码错误。' }),
    })
  })
}

async function mockInvitationBootstrap(page: Page) {
  await page.route('**/api/auth/setup-status', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ initialized: true, authentication_ready: true }),
    })
  })
  await page.route('**/api/settings/branding', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        product_name: 'SAGE',
        product_subtitle: 'RESEARCH ARCHIVE',
        organization_name: 'SAGE Lab',
        slogan: '科学 · 数据 · 成长 · 卓越',
        slogan_secondary: 'Science · Archive · Growth · Excellence',
        primary_color: '#2E7351',
        logo_url: null,
        revision: 'revision-1',
      }),
    })
  })
}

test('邀请页切换 token 时取消旧请求并清空密码', async ({ page }) => {
  await mockInvitationBootstrap(page)
  const requestedTokens: string[] = []
  let releaseSlowRequest!: () => void
  const slowRequest = new Promise<void>((resolve) => { releaseSlowRequest = resolve })
  await page.route('**/api/auth/invitations', async (route) => {
    const invitationToken = route.request().headers()['x-sage-invitation-token'] ?? ''
    requestedTokens.push(invitationToken)
    if (invitationToken === 'slow-token') await slowRequest
    const username = invitationToken === 'first-token' ? 'first-admin' : invitationToken === 'final-token' ? 'final-admin' : 'stale-admin'
    try {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          username,
          purpose: 'recovery',
          expires_at: '2099-01-01T00:00:00Z',
        }),
      })
    } catch {
      // An aborted stale invitation request no longer has a response channel.
    }
  })

  await page.goto('/register/first-token')
  await expect(page.getByText('first-admin', { exact: true })).toBeVisible()
  await page.getByLabel('密码', { exact: true }).fill('sensitive-password')
  await page.getByLabel('确认密码').fill('sensitive-password')

  await page.evaluate(() => {
    window.history.pushState({}, '', '/register/slow-token')
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  await expect.poll(() => requestedTokens.includes('slow-token')).toBe(true)
  await page.evaluate(() => {
    window.history.pushState({}, '', '/register/final-token')
    window.dispatchEvent(new PopStateEvent('popstate'))
  })

  await expect(page.getByText('final-admin', { exact: true })).toBeVisible()
  await expect(page.getByLabel('密码', { exact: true })).toHaveValue('')
  await expect(page.getByLabel('确认密码')).toHaveValue('')
  releaseSlowRequest()
  await expect(page.getByText('final-admin', { exact: true })).toBeVisible()
  await expect(page.getByText('stale-admin', { exact: true })).toBeHidden()
  expect(requestedTokens).toEqual(['first-token', 'slow-token', 'final-token'])
})

test('空实例引导创建唯一的首个管理员', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 740 })
  await mockEmptyDashboard(page)
  await page.route('**/api/auth/setup-status', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ initialized: false, authentication_ready: true }),
    })
  })
  let setupPayload: unknown
  await page.route('**/api/auth/setup', async (route) => {
    setupPayload = route.request().postDataJSON()
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        id: '10101010-1010-1010-1010-101010101010',
        username: 'owner',
        name: '实例管理员',
        email: 'owner@example.org',
        role: 'admin',
        upload_username: 'owner',
        is_active: true,
        is_instance_owner: true,
        is_registered: true,
        session_token: 'setup-session-token',
      }),
    })
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: '初始化管理员' })).toBeVisible()
  await page.getByLabel('管理员账号').fill('owner')
  await page.getByLabel('显示名称').fill('实例管理员')
  await page.getByLabel('邮箱').fill('owner@example.org')
  await page.getByLabel('管理员密码').fill('owner-password')
  await page.getByLabel('确认密码').fill('different-password')
  await expect(page.getByRole('button', { name: '创建管理员并进入系统' })).toBeDisabled()
  await expect(page.getByRole('alert')).toHaveText('两次输入的密码不一致。')
  await page.getByLabel('确认密码').fill('owner-password')
  await page.getByRole('button', { name: '创建管理员并进入系统' }).click()

  await expect(page.getByRole('button', { name: '账户菜单：实例管理员' })).toBeVisible()
  expect(setupPayload).toEqual({
    username: 'owner',
    name: '实例管理员',
    email: 'owner@example.org',
    password: 'owner-password',
  })
  await expect.poll(
    () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true)
})

test('已初始化实例缺少签名密钥时直接阻止登录', async ({ page }) => {
  await page.route('**/api/auth/setup-status', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ initialized: true, authentication_ready: false }),
    })
  })

  await page.goto('/')

  await expect(page.getByRole('alert')).toContainText('认证服务尚未配置')
  await expect(page.getByRole('button', { name: '重新检查' })).toBeVisible()
  await expect(page.getByRole('button', { name: '进入归档系统' })).toBeHidden()
})

async function mockEmptyDashboard(page: Page) {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
}

async function mockEmptySettingsCollections(page: Page) {
  await page.route('**/api/auth/admin-accounts', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: '[]' })
  })
  await page.route('**/api/auth/access-tokens', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: '[]' })
  })
}

async function mockEmptyCatalogue(page: Page) {
  await page.route('**/api/assets?*', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 20, publication_facets: null }),
    })
  })
}

async function mockLiteratureFacets(page: Page) {
  await page.route('**/api/assets?*', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        items: [],
        total: 0,
        page: 1,
        page_size: 20,
        publication_facets: { venues: ['ICLR'], years: [2026] },
      }),
    })
  })
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

test('上传入库成功后目录刷新失败仍保留成功结果', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 1, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  await signInWithMockAccount(page)
  let catalogueReads = 0
  const asset = {
    id: '82828282-8282-8282-8282-828282828282',
    type: 'literature',
    slug: 'test-literature',
    title: '测试文献',
    summary: '用于验证上传完成后的目录同步语义。',
    status: 'published',
    visibility: 'lab',
    owner: { id: '83838383-8383-8383-8383-838383838383', name: '测试用户', avatar_url: null },
    details: {
      venue: 'ACL', year: 2026, track: 'Conference Paper', authors: ['Ada Lovelace'],
      source_id: 'test-literature', source_url: 'https://example.com/test', pdf_url: 'https://example.com/test.pdf',
    },
    tags: ['ACL'],
    current_version: null,
    total_size: 0,
    file_count: 0,
    upload_directories: [{ name: 'original', label: '原始文件' }],
    default_upload_directory: 'original',
    updated_at: '2026-08-14T03:00:00Z',
  }
  await page.route('**/api/assets?*', async (route) => {
    catalogueReads += 1
    if (catalogueReads === 1) {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          items: [asset], total: 1, page: 1, page_size: 20,
          publication_facets: { venues: ['ACL'], years: [2026] },
        }),
      })
      return
    }
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: '目录暂不可用' }),
    })
  })
  await page.route('**/api/archive/upload-command', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        upload_id: '81818181-8181-8181-8181-818181818181',
        asset_id: '82828282-8282-8282-8282-828282828282',
        asset_title: '测试文献',
        archive_relative_path: 'literature/test/original',
        staging_relative_path: '.uploads/81818181-8181-8181-8181-818181818181',
        upload_token: 'upload-token',
        expires_at: '2026-08-15T03:00:00Z',
        command: 'scp paper.pdf archive',
      }),
    })
  })
  await page.route('**/api/archive/uploads/*/status', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        upload_id: '81818181-8181-8181-8181-818181818181',
        status: 'ready',
        uploaded_file_count: 1,
        total_size: 2048,
        expires_at: '2026-08-15T03:00:00Z',
      }),
    })
  })
  let finalizeRequests = 0
  await page.route('**/api/archive/uploads/*/finalize', async (route) => {
    finalizeRequests += 1
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        asset_id: '82828282-8282-8282-8282-828282828282',
        imported_file_count: 1,
        total_size: 2048,
        relative_paths: ['literature/test/original/paper.pdf'],
        checksums: {},
      }),
    })
  })

  await page.goto('/literature?view=grid')
  await page.getByRole('button', { name: '上传文件' }).first().click()
  await page.getByLabel('本机待上传路径').fill('/tmp/paper.pdf')
  await page.getByRole('button', { name: '生成上传命令' }).click()
  await page.getByRole('button', { name: '检测并入库' }).click()

  await expect(page.getByText('文件已完成入库')).toBeVisible()
  await expect(page.getByText('文件已入库，但目录暂时无法刷新。')).toBeVisible()
  await expect(page.getByText('literature/test/original/paper.pdf')).toBeVisible()
  await expect(page.getByRole('button', { name: '检测并入库' })).toHaveCount(0)
  expect(finalizeRequests).toBe(1)
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

test('新实例总览为尚无数据的面板提供明确状态', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  await signIn(page)

  await expect(page.getByText('尚未登记科研资产。完成首次登记后，最近归档会显示在这里。')).toBeVisible()
  await expect(page.getByText('尚无归档活动。资产登记、更新和文件操作会记录在这里。')).toBeVisible()
  await expect(page.getByText('尚无知识标签。为资产添加标签后，会形成团队共享词表。')).toBeVisible()
  await expect(page.getByRole('link', { name: '前往论文目录' })).toHaveAttribute('href', '/papers')
})

test('近期活动折叠重复事件并支持系统级操作', async ({ page }) => {
  const componentResolutionWarnings: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'warning' && message.text().includes('Failed to resolve component')) {
      componentResolutionWarnings.push(message.text())
    }
  })
  const assetId = '12121212-1212-1212-1212-121212121212'
  const activity = {
    id: '34343434-3434-3434-3434-343434343434',
    asset_id: assetId,
    asset_title: '重复上传测试文献',
    asset_type: 'literature',
    actor_name: '测试用户',
    credential_name: null,
    action: 'prepared_upload',
    action_label: '生成上传指令',
    description: '为 literature/repeated-upload/original 生成了上传指令',
    created_at: '2026-08-14T01:00:00Z',
    occurrence_count: 18,
  }
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 1, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        popular_tags: [],
        recent_activities: [{
          ...activity,
          id: '56565656-5656-5656-5656-565656565656',
          asset_id: null,
          asset_title: null,
          asset_type: null,
          action: 'updated_branding',
          action_label: '更新品牌设置',
          description: '更新了品牌设置',
          occurrence_count: 2,
        }],
      }),
    })
  })
  await signInWithMockAccount(page)
  await expect(page.locator('.activity-list').getByText('更新品牌设置', { exact: true })).toBeVisible()
  await expect(page.locator('.activity-list').getByText('×2', { exact: true })).toBeVisible()

  await page.route(`**/api/assets/${assetId}`, async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        id: assetId,
        type: 'literature',
        slug: 'repeated-upload',
        title: '重复上传测试文献',
        summary: '用于验证活动摘要。',
        status: 'published',
        visibility: 'lab',
        owner: { id: '78787878-7878-7878-7878-787878787878', name: '测试用户', avatar_url: null },
        details: { venue: 'arXiv', year: 2026, authors: ['Ada Lovelace'] },
        tags: [],
        current_version: null,
        total_size: 0,
        file_count: 0,
        upload_directories: [],
        default_upload_directory: 'original',
        updated_at: '2026-08-14T01:00:00Z',
        versions: [],
        files: [],
        related_assets: [],
        recent_activities: [activity],
      }),
    })
  })
  await page.route(`**/api/assets/${assetId}/citation/bibtex`, async (route) => {
    await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: '无引用' }) })
  })
  await page.goto(`/assets/${assetId}?returnTo=/literature`)
  await expect(page.getByRole('heading', { name: '近期活动' })).toBeVisible()
  await expect(page.getByText('生成上传指令 ×18', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '查看完整日志' })).toHaveAttribute('href', '/activity-log')
  expect(componentResolutionWarnings).toEqual([])
})

test('窄屏顶栏与目录保持在视口内', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 760 })
  await signIn(page)
  await page.goto('/literature?view=grid')
  await expect(page.getByRole('heading', { name: '文献目录' })).toBeVisible()

  const assertNoHorizontalOverflow = async () => {
    const layout = await page.evaluate(() => ({
      viewportWidth: document.documentElement.clientWidth,
      documentWidth: document.documentElement.scrollWidth,
    }))
    expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth)
  }

  await assertNoHorizontalOverflow()
  const assetWithFiles = page.locator('.catalogue-card').filter({ hasText: /已有数据/ }).first()
  await assetWithFiles.getByRole('link', { name: /查看详情/ }).click()
  await expect(page.getByRole('heading', { name: '文件浏览' })).toBeVisible()
  await assertNoHorizontalOverflow()
})

test('账户菜单明确区分资料与退出操作', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  await signInWithMockAccount(page)
  const accountMenu = page.getByRole('button', { name: /账户菜单/ })
  await accountMenu.click()
  await expect(page.getByRole('button', { name: '退出登录' })).toBeVisible()
  await expect(page.getByText('test-admin@sage.test')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('button', { name: '退出登录' })).toBeHidden()
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
  await mockRejectedLogin(page)
  await page.goto('/')
  await expect(page.getByRole('button', { name: '进入归档系统' })).toBeVisible()
  await page.evaluate(() => window.localStorage.setItem('sage-session-token', 'unrelated-token'))
  await page.getByLabel('账号').fill('zhengyu')
  await page.getByLabel('密码').fill('wrong-password')
  await page.getByRole('button', { name: '进入归档系统' }).click()

  await expect(page.getByRole('alert')).toHaveText('账号或密码错误。')
  await expect(page.getByLabel('账号')).toHaveAttribute('aria-describedby', 'login-error')
  await expect(page.getByLabel('密码')).toHaveAttribute('aria-describedby', 'login-error')
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem('sage-session-token'))).toBe('unrelated-token')
})

test('登录页密码框提供清晰焦点与错误播报', async ({ page }) => {
  await mockRejectedLogin(page)
  await page.goto('/')
  await page.getByLabel('账号').focus()
  await page.keyboard.press('Tab')
  await expect(page.getByLabel('密码')).toBeFocused()
  const primaryColor = await page.evaluate(() => {
    const probe = document.createElement('span')
    probe.style.color = 'var(--sage)'
    document.body.append(probe)
    const color = getComputedStyle(probe).color
    probe.remove()
    return color
  })
  await expect(page.locator('.password-field')).toHaveCSS('border-color', primaryColor)
  await expect(page.locator('.password-field')).not.toHaveCSS('box-shadow', 'none')

  await page.getByLabel('账号').fill('zhengyu')
  await page.getByLabel('密码').fill('wrong-password')
  await page.getByRole('button', { name: '进入归档系统' }).click()
  await expect(page.getByRole('alert')).toHaveText('账号或密码错误。')
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

test('设置分区导航支持快速定位并适配窄屏', async ({ page }) => {
  await mockEmptyDashboard(page)
  await mockEmptySettingsCollections(page)
  await signInWithMockAccount(page)
  await page.goto('/settings')

  const sectionNavigation = page.getByRole('navigation', { name: '设置分区' })
  await expect(sectionNavigation.getByRole('link')).toHaveCount(4)
  await sectionNavigation.getByRole('link', { name: '系统与更新' }).click()
  await expect(page).toHaveURL(/#settings-system-update/)
  await expect(page.locator('#settings-system-update')).toBeInViewport()
  await expect.poll(() => page.locator('#settings-system-update').evaluate((element) => element.getBoundingClientRect().top)).toBeGreaterThanOrEqual(120)

  await page.setViewportSize({ width: 390, height: 844 })
  await sectionNavigation.getByRole('link', { name: 'AI 访问令牌' }).click()
  await expect(page).toHaveURL(/#settings-agent-access/)
  await expect(page.locator('#settings-agent-access')).toBeInViewport()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBe(390)
})

test('设置读取挂起时可中止并重新加载', async ({ page }) => {
  let brandingRequests = 0
  let releaseHangingRequest!: () => void
  const hangingRequest = new Promise<void>((resolve) => { releaseHangingRequest = resolve })
  const brandingPayload = {
    product_name: 'SAGE',
    product_subtitle: 'RESEARCH ARCHIVE',
    organization_name: 'SAGE Lab',
    slogan: '科学 · 数据 · 成长 · 卓越',
    slogan_secondary: 'Science · Archive · Growth · Excellence',
    primary_color: '#2E7351',
    logo_url: null,
    revision: 'revision-1',
  }
  await page.route('**/api/settings/branding', async (route) => {
    brandingRequests += 1
    if (brandingRequests === 2) {
      await hangingRequest
      try {
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify(brandingPayload) })
      } catch {
        // The refresh intentionally aborted this request.
      }
      return
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(brandingPayload) })
  })
  await mockEmptyDashboard(page)
  await mockEmptySettingsCollections(page)
  await signInWithMockAccount(page)
  await navigateTo(page, '系统设置')

  await expect.poll(() => brandingRequests).toBe(2)
  const refresh = page.getByRole('button', { name: '刷新' })
  await expect(refresh).toBeEnabled()
  await refresh.click()
  await expect.poll(() => brandingRequests).toBe(3)
  await expect(page.getByLabel('产品名称')).toHaveValue('SAGE')
  releaseHangingRequest()
})

test('品牌设置保存后立即更新全站标识', async ({ page }) => {
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
        revision: 'revision-2',
      }),
    })
  })
  await mockEmptyDashboard(page)
  await mockEmptySettingsCollections(page)
  await signInWithMockAccount(page)
  await page.goto('/settings')
  await page.getByLabel('产品名称').fill('Atlas')
  await page.getByLabel('产品副标题').fill('DATA MANAGER')
  await page.getByLabel('组织名称').fill('Atlas Institute')
  await page.getByLabel('主标语').fill('研究 · 连接 · 积累')
  await page.getByLabel('辅助标语').fill('Research · Connect · Preserve')
  await page.getByLabel('品牌主色色值').fill('#245B78')
  await page.getByRole('button', { name: '保存文字与主色' }).click()
  await expect(page.getByText('品牌设置已应用')).toBeVisible()
  await expect(page.locator('.brand').getByText('Atlas', { exact: true })).toBeVisible()
  await expect(page).toHaveTitle('系统设置 · Atlas')
})

test('品牌写操作共享同一个事务状态', async ({ page }) => {
  let releaseBrandingUpdate!: () => void
  const brandingUpdateGate = new Promise<void>((resolve) => { releaseBrandingUpdate = resolve })
  await page.route('**/api/settings/branding', async (route) => {
    if (route.request().method() !== 'PATCH') {
      await route.continue()
      return
    }
    await brandingUpdateGate
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        product_name: 'SAGE',
        product_subtitle: 'RESEARCH ARCHIVE',
        organization_name: 'SAGE Lab',
        slogan: '科学 · 数据 · 成长 · 卓越',
        slogan_secondary: 'Science · Archive · Growth · Excellence',
        primary_color: '#2E7351',
        logo_url: null,
        revision: 'revision-2',
      }),
    })
  })
  await mockEmptyDashboard(page)
  await mockEmptySettingsCollections(page)
  await signInWithMockAccount(page)
  await page.goto('/settings')

  await page.getByLabel('产品名称').fill('SAGE Archive')
  await page.getByRole('button', { name: '保存文字与主色' }).click()
  await expect(page.getByRole('button', { name: '正在保存' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '刷新' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '选择图片' })).toBeDisabled()
  await expect(page.getByLabel('选择实例 Logo 图片')).toBeDisabled()
  await expect(page.getByLabel('产品名称')).toBeDisabled()
  await expect(page.getByLabel('品牌主色色值')).toBeDisabled()

  releaseBrandingUpdate()
  await expect(page.getByText('品牌设置已应用')).toBeVisible()
  await expect(page.getByRole('button', { name: '保存文字与主色' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '选择图片' })).toBeEnabled()
  await expect(page.getByLabel('产品名称')).toBeEnabled()
})

test('Logo 选择后先预览，明确应用时才上传', async ({ page }) => {
  const pngBase64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
  let logoWrites = 0
  await page.route('**/api/settings/branding/logo', async (route) => {
    logoWrites += 1
    expect(route.request().headers()['x-sage-branding-revision']).toBe('revision-1')
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        product_name: 'SAGE',
        product_subtitle: 'RESEARCH ARCHIVE',
        organization_name: 'SAGE Lab',
        slogan: '科学 · 数据 · 成长 · 卓越',
        slogan_secondary: 'Science · Archive · Growth · Excellence',
        primary_color: '#2E7351',
        logo_url: `data:image/png;base64,${pngBase64}`,
        revision: 'revision-2',
      }),
    })
  })
  await page.route('**/api/settings/branding', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        product_name: 'SAGE',
        product_subtitle: 'RESEARCH ARCHIVE',
        organization_name: 'SAGE Lab',
        slogan: '科学 · 数据 · 成长 · 卓越',
        slogan_secondary: 'Science · Archive · Growth · Excellence',
        primary_color: '#2E7351',
        logo_url: null,
        revision: 'revision-1',
      }),
    })
  })
  await mockEmptyDashboard(page)
  await mockEmptySettingsCollections(page)
  await signInWithMockAccount(page)
  await page.goto('/settings')

  await page.getByLabel('选择实例 Logo 图片').setInputFiles({
    name: 'brand.png',
    mimeType: 'image/png',
    buffer: Buffer.from(pngBase64, 'base64'),
  })
  expect(logoWrites).toBe(0)
  await expect(page.locator('.brand-preview img')).toHaveAttribute('src', /^blob:/)
  await page.getByRole('button', { name: '应用 Logo' }).click()

  await expect.poll(() => logoWrites).toBe(1)
  await expect(page.getByText('Logo 已应用')).toBeVisible()
})

test('系统版本读取失败可局部重试', async ({ page }) => {
  await mockEmptyDashboard(page)
  await mockEmptySettingsCollections(page)
  await signInWithMockAccount(page)
  await page.unroute('**/api/settings/system-update')

  let statusRequests = 0
  await page.route('**/api/settings/system-update', async (route) => {
    statusRequests += 1
    if (statusRequests === 1) {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: '更新代理暂时不可用' }),
      })
      return
    }
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(systemUpdateStatus({
        enabled: true,
        state: 'idle',
        message: '当前已经是最新版本。',
        current_commit: 'a'.repeat(40),
        latest_commit: 'a'.repeat(40),
        worktree_clean: true,
      })),
    })
  })

  await page.goto('/settings')
  const updatePanel = page.locator('.system-update-panel')
  const loadError = updatePanel.getByRole('alert')
  await expect(loadError).toContainText('更新代理暂时不可用')

  await loadError.getByRole('button', { name: '重试' }).click()

  await expect(updatePanel.getByText('当前已经是最新版本。')).toBeVisible()
  await expect(loadError).toBeHidden()
  expect(statusRequests).toBe(2)
})

test('检查更新在后台运行并由页面轮询结果', async ({ page }) => {
  await mockEmptyDashboard(page)
  await mockEmptySettingsCollections(page)
  await signInWithMockAccount(page)
  await page.unroute('**/api/settings/system-update')

  const currentCommit = 'a'.repeat(40)
  const latestCommit = 'b'.repeat(40)
  let checkRequests = 0
  let updateState = systemUpdateStatus({
    enabled: true,
    state: 'idle',
    message: '当前已经是最新版本。',
    current_commit: currentCommit,
    latest_commit: currentCommit,
    worktree_clean: true,
  })
  await page.route('**/api/settings/system-update', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(updateState) })
  })
  await page.route('**/api/settings/system-update/check', async (route) => {
    checkRequests += 1
    updateState = systemUpdateStatus({
      ...updateState,
      state: 'checking',
      phase: 'fetch',
      message: '正在连接 GitHub 获取 origin/main…',
    })
    await route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify(updateState),
    })
  })

  await page.goto('/settings')
  const updatePanel = page.locator('.system-update-panel')
  const checkButton = updatePanel.getByRole('button', { name: '检查更新' })
  await checkButton.click()

  await expect(updatePanel.getByRole('button', { name: '正在检查' })).toBeDisabled()
  await expect(updatePanel.getByText('正在连接 GitHub 获取 origin/main…')).toBeVisible()
  await expect(updatePanel).not.toContainText('无法连接宿主机更新服务')

  updateState = systemUpdateStatus({
    ...updateState,
    state: 'available',
    phase: null,
    message: '发现 1 个可用提交。',
    latest_commit: latestCommit,
    update_available: true,
    behind_count: 1,
  })
  await expect(updatePanel.getByText('发现 1 个可用提交。')).toBeVisible({ timeout: 5_000 })
  await expect(updatePanel.getByRole('button', { name: '检查更新' })).toBeEnabled()
  expect(checkRequests).toBe(1)
})

test('系统更新轮询失败后退避并自动恢复', async ({ page }) => {
  await mockEmptyDashboard(page)
  await mockEmptySettingsCollections(page)
  await signInWithMockAccount(page)
  await page.unroute('**/api/settings/system-update')

  let statusRequests = 0
  let allowRecovery = false
  const busyStatus = systemUpdateStatus({
    enabled: true,
    state: 'checking',
    phase: 'fetch',
    message: '正在连接 GitHub 获取 origin/main…',
    current_commit: 'a'.repeat(40),
    latest_commit: 'a'.repeat(40),
    worktree_clean: true,
  })
  await page.route('**/api/settings/system-update', async (route) => {
    statusRequests += 1
    if (statusRequests === 1) {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(busyStatus) })
      return
    }
    if (!allowRecovery) {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: '更新代理暂时不可用' }),
      })
      return
    }
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(systemUpdateStatus({
        ...busyStatus,
        state: 'idle',
        phase: null,
        message: '当前已经是最新版本。',
      })),
    })
  })

  await page.goto('/settings')
  const updatePanel = page.locator('.system-update-panel')
  await expect(updatePanel.getByText('正在连接 GitHub 获取 origin/main…')).toBeVisible()
  await expect.poll(() => statusRequests, { timeout: 5_000 }).toBe(2)

  const pollError = updatePanel.locator('.system-update-error')
  await expect(pollError).toContainText('更新代理暂时不可用')
  await expect(pollError).toContainText('将在 4 秒后重试')
  await page.waitForTimeout(2_500)
  expect(statusRequests).toBe(2)

  allowRecovery = true
  await expect(updatePanel.getByText('当前已经是最新版本。')).toBeVisible({ timeout: 5_000 })
  await expect(pollError).toBeHidden()
  expect(statusRequests).toBe(3)
})

test('实例所有者可在窄屏安全启动系统更新', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockEmptyDashboard(page)
  await mockEmptySettingsCollections(page)
  await signInWithMockAccount(page)
  await page.unroute('**/api/settings/system-update')

  const currentCommit = 'a'.repeat(40)
  const latestCommit = 'b'.repeat(40)
  let updateState = systemUpdateStatus({
    enabled: true,
    state: 'available',
    message: '发现 2 个可用提交。',
    current_commit: currentCommit,
    latest_commit: latestCommit,
    update_available: true,
    behind_count: 2,
    worktree_clean: true,
    checked_at: '2026-08-15T08:01:00Z',
    remote_url: 'https://github.com/Zheng-Yu7463/SageDataManager.git',
    commits: [
      {
        sha: latestCommit,
        short_sha: 'bbbbbbbb',
        subject: 'feat: improve archive update flow',
        author: 'SAGE Maintainer',
        committed_at: '2026-08-15T08:00:00Z',
      },
      {
        sha: 'c'.repeat(40),
        short_sha: 'cccccccc',
        subject: 'fix: preserve update status',
        author: 'SAGE Maintainer',
        committed_at: '2026-08-15T07:00:00Z',
      },
    ],
  })
  await page.route('**/api/settings/system-update', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(updateState) })
  })

  const applyPayloads: unknown[] = []
  await page.route('**/api/settings/system-update/apply', async (route) => {
    applyPayloads.push(route.request().postDataJSON())
    updateState = systemUpdateStatus({
      ...updateState,
      state: 'backing_up',
      phase: 'backing_up',
      message: '正在备份 PostgreSQL 数据库…',
      started_at: '2026-08-15T08:05:00Z',
      logs: ['[08:05:00] Update accepted', '[08:05:01] Backing up PostgreSQL'],
    })
    await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify(updateState) })
  })

  await page.goto('/settings')
  const updatePanel = page.locator('.system-update-panel')
  await expect(updatePanel.getByRole('heading', { name: '系统与更新' })).toBeVisible()
  await expect(updatePanel).toContainText('aaaaaaaa')
  await expect(updatePanel).toContainText('bbbbbbbb')
  await expect(updatePanel).toContainText('feat: improve archive update flow')
  await expect.poll(
    () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true)
  const panelBox = await updatePanel.boundingBox()
  expect(panelBox).not.toBeNull()
  expect(panelBox!.x).toBeGreaterThanOrEqual(0)
  expect(panelBox!.x + panelBox!.width).toBeLessThanOrEqual(390)

  await updatePanel.getByRole('button', { name: '立即更新' }).click()
  const dialog = page.getByRole('alertdialog', { name: '更新到 bbbbbbbb' })
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('2 个提交')
  await dialog.getByLabel('确认当前账号密码').fill('test-password')
  await dialog.getByRole('button', { name: '备份并更新' }).click()

  await expect(dialog).toBeHidden()
  await expect(updatePanel.getByText('正在备份 PostgreSQL 数据库…')).toBeVisible()
  await expect(updatePanel.locator('.system-update-progress')).toBeVisible()
  expect(applyPayloads).toEqual([{ password: 'test-password', target_commit: latestCommit }])

  updateState = systemUpdateStatus({
    ...updateState,
    state: 'succeeded',
    phase: 'complete',
    message: '系统已更新并通过健康检查。',
    current_commit: latestCommit,
    update_available: false,
    behind_count: 0,
    completed_at: '2026-08-15T08:08:00Z',
    backup_path: 'sage-test.dump',
  })
  await expect(updatePanel.getByText('系统已更新并通过健康检查。')).toBeVisible({ timeout: 5_000 })
  const completedProgress = updatePanel.getByRole('progressbar', { name: '系统更新进度' })
  await expect(completedProgress).toHaveAttribute('aria-valuenow', '100')
  await expect(completedProgress.locator('span')).toHaveAttribute('style', /width: 100%/)
  await expect(updatePanel.getByRole('button', { name: '刷新到新版本' })).toBeVisible()
})

test('设置页令牌加载失败不影响管理员账号事实', async ({ page }) => {
  await page.route('**/api/settings/branding', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        product_name: 'SAGE',
        product_subtitle: 'RESEARCH ARCHIVE',
        organization_name: 'SAGE Lab',
        slogan: '科学 · 数据 · 成长 · 卓越',
        slogan_secondary: 'Science · Archive · Growth · Excellence',
        primary_color: '#2E7351',
        logo_url: null,
        revision: 'revision-1',
      }),
    })
  })
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  await page.route('**/api/auth/admin-accounts', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify([{
        id: '45454545-4545-4545-4545-454545454545',
        username: 'testadmin',
        name: '测试管理员',
        email: 'test-admin@sage.test',
        role: 'admin',
        upload_username: 'testadmin',
        is_active: true,
        is_instance_owner: true,
        is_registered: true,
      }]),
    })
  })
  await page.route('**/api/auth/access-tokens', async (route) => {
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: '令牌服务暂时不可用' }),
    })
  })
  await signInWithMockAccount(page)
  await page.goto('/settings')

  await expect(page.getByRole('heading', { name: '管理员账号' })).toBeVisible()
  await expect(page.locator('.accounts-table')).toContainText('testadmin')
  await expect(page.locator('.agent-access-error')).toContainText('令牌服务暂时不可用')
  await expect(page.getByText('没有有效的 AI 访问令牌')).toBeHidden()
  await expect(page.getByRole('button', { name: '刷新' })).toBeEnabled()
})

test('并发更新管理员时各行保持独立状态', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  const managedAccounts = ['alpha', 'beta'].map((username, index) => ({
    id: `${index + 1}5656565-5656-5656-5656-565656565656`,
    username,
    name: `${username.toUpperCase()} 管理员`,
    email: `${username}@sage.test`,
    role: 'admin',
    upload_username: username,
    is_active: true,
    is_instance_owner: false,
    is_registered: true,
  }))
  await page.route('**/api/auth/admin-accounts', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify([
      {
        id: '90909090-9090-9090-9090-909090909090',
        username: 'testadmin',
        name: '测试管理员',
        email: 'test-admin@sage.test',
        role: 'admin',
        upload_username: 'testadmin',
        is_active: true,
        is_instance_owner: true,
        is_registered: true,
      },
      ...managedAccounts,
    ]) })
  })
  await page.route('**/api/auth/access-tokens', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: '[]' })
  })
  const updateReleases = new Map<string, () => void>()
  await page.route('**/api/auth/admin-accounts/*', async (route) => {
    const username = route.request().url().split('/').at(-1)!
    await new Promise<void>((resolve) => updateReleases.set(username, resolve))
    const account = managedAccounts.find((item) => item.username === username)!
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...account, is_active: false }) })
  })
  await signInWithMockAccount(page)
  await page.goto('/settings')

  const alphaRow = page.locator('.account-row').filter({ hasText: 'alpha@sage.test' })
  const betaRow = page.locator('.account-row').filter({ hasText: 'beta@sage.test' })
  await alphaRow.getByRole('button', { name: '停用管理员：ALPHA 管理员' }).click()
  await betaRow.getByRole('button', { name: '停用管理员：BETA 管理员' }).click()
  await expect(alphaRow.getByRole('button', { name: '正在处理：ALPHA 管理员' })).toBeDisabled()
  await expect(betaRow.getByRole('button', { name: '正在处理：BETA 管理员' })).toBeDisabled()

  updateReleases.get('alpha')?.()
  await expect(alphaRow.getByRole('button', { name: '启用管理员：ALPHA 管理员' })).toBeEnabled()
  await expect(betaRow.getByRole('button', { name: '正在处理：BETA 管理员' })).toBeDisabled()

  updateReleases.get('beta')?.()
  await expect(betaRow.getByRole('button', { name: '启用管理员：BETA 管理员' })).toBeEnabled()
})

test('管理员邀请链接生成保持单飞', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  const managedAccounts = ['alpha', 'beta'].map((username, index) => ({
    id: `${index + 1}6767676-6767-6767-6767-676767676767`,
    username,
    name: `${username.toUpperCase()} 管理员`,
    email: `${username}@sage.test`,
    role: 'admin',
    upload_username: username,
    is_active: true,
    is_instance_owner: false,
    is_registered: true,
  }))
  await page.route('**/api/auth/admin-accounts', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify([
      {
        id: '90909090-9090-9090-9090-909090909090',
        username: 'testadmin',
        name: '测试管理员',
        email: 'test-admin@sage.test',
        role: 'admin',
        upload_username: 'testadmin',
        is_active: true,
        is_instance_owner: true,
        is_registered: true,
      },
      ...managedAccounts,
    ]) })
  })
  await page.route('**/api/auth/access-tokens', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: '[]' })
  })

  const invitationRequests: string[] = []
  let releaseInvitation!: () => void
  const invitationPending = new Promise<void>((resolve) => { releaseInvitation = resolve })
  await page.route('**/api/auth/admin-accounts/*/recovery-invitation', async (route) => {
    const username = new URL(route.request().url()).pathname.split('/').at(-2)!
    invitationRequests.push(username)
    await invitationPending
    const account = managedAccounts.find((item) => item.username === username)!
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        account,
        registration_path: `/register/recovery-${username}`,
        expires_at: '2099-01-01T00:00:00Z',
        purpose: 'recovery',
      }),
    })
  })

  await signInWithMockAccount(page)
  await page.goto('/settings')

  const alphaInvitation = page.getByRole('button', { name: '生成密码恢复链接：ALPHA 管理员' })
  await alphaInvitation.click()
  await expect.poll(() => invitationRequests).toEqual(['alpha'])
  await expect(page.getByRole('button', { name: '正在生成邀请链接：ALPHA 管理员' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '请等待当前邀请链接生成完成：BETA 管理员' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '新增管理员' })).toBeDisabled()

  releaseInvitation()
  const dialog = page.getByRole('dialog', { name: '密码恢复链接已创建' })
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('请将链接私下发送给 alpha')
  expect(invitationRequests).toEqual(['alpha'])

  await dialog.getByRole('button', { name: '完成' }).click()
  await expect(dialog).toBeHidden()
})

test('AI 访问令牌保护一次性明文并归档失效记录', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  await page.route('**/api/auth/admin-accounts', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify([{
        id: '90909090-9090-9090-9090-909090909090',
        username: 'testadmin',
        name: '测试管理员',
        email: 'test-admin@sage.test',
        role: 'admin',
        upload_username: 'testadmin',
        is_active: true,
        is_instance_owner: true,
        is_registered: true,
      }]),
    })
  })
  await signInWithMockAccount(page)
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
  let submittedTokenScopes: string[] = []
  await page.route('**/api/auth/access-tokens', async (route) => {
    if (route.request().method() === 'POST') {
      submittedTokenScopes = (route.request().postDataJSON() as { scopes: string[] }).scopes
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
  const uploadScope = createDialog.getByRole('button', { name: /上传文件/ })
  const finalizeScope = createDialog.getByRole('button', { name: /正式入库/ })
  await finalizeScope.click()
  await expect(uploadScope).toHaveAttribute('aria-pressed', 'true')
  await uploadScope.click()
  await expect(uploadScope).toHaveAttribute('aria-pressed', 'false')
  await expect(finalizeScope).toHaveAttribute('aria-pressed', 'false')
  await finalizeScope.click()
  await expect(uploadScope).toHaveAttribute('aria-pressed', 'true')
  await expect(finalizeScope).toHaveAttribute('aria-pressed', 'true')
  await createDialog.getByRole('button', { name: '创建令牌' }).click()
  expect(submittedTokenScopes).toEqual(expect.arrayContaining(['files:upload', 'archive:finalize']))

  const createdDialog = page.getByRole('dialog', { name: '令牌已创建' })
  await expect(createdDialog).toContainText(createdToken.token)
  await page.keyboard.press('Escape')
  await expect(createdDialog).toBeVisible()
  await expect(createdDialog.getByRole('button', { name: '关闭' })).toHaveCount(0)

  await createdDialog.getByRole('button', { name: '我已安全保存' }).click()
  await expect(page.getByText(createdToken.token, { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '撤销令牌：自动化验收' }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: '确认撤销' }).click()

  const history = page.getByRole('button', { name: /历史令牌/ })
  await expect(history).toHaveAttribute('aria-expanded', 'false')
  await history.click()
  await expect(page.locator('.token-list--history')).toContainText('自动化验收')
  await expect(page.locator('.token-list--history')).toContainText('已撤销')
})

test('目录筛选与视图状态可通过 URL 恢复', async ({ page }) => {
  await signIn(page)
  await mockLiteratureFacets(page)
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
  await mockLiteratureFacets(page)
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

test('目录无匹配结果可一次清除搜索与筛选', async ({ page }) => {
  await signIn(page)
  await page.goto('/literature?q=definitely-no-such-publication&venue=ICLR&view=grid')

  await expect(page.getByText('当前搜索和筛选条件没有匹配结果。')).toBeVisible()
  await page.getByRole('button', { name: '清除搜索与筛选' }).click()

  await expect(page).toHaveURL('/literature?view=grid')
  await expect(page.locator('.catalogue-card').first()).toBeVisible()
})

test('连续搜索只显示最新请求的状态和结果', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 1, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  await signInWithMockAccount(page)
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

test('切换搜索词后不会把旧结果显示在新查询下', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 1, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  await signInWithMockAccount(page)
  let releaseBeta: (() => void) | undefined
  const betaReleased = new Promise<void>((resolve) => { releaseBeta = resolve })
  let markBetaStarted: (() => void) | undefined
  const betaStarted = new Promise<void>((resolve) => { markBetaStarted = resolve })
  const result = {
    id: '51515151-5151-5151-5151-515151515151',
    type: 'project',
    slug: 'alpha-result',
    title: 'Alpha 旧结果',
    summary: '不应显示在 Beta 查询下',
    status: 'active',
    visibility: 'lab',
    owner: { id: '52525252-5252-5252-5252-525252525252', name: '测试用户', avatar_url: null },
    details: {},
    tags: [],
    current_version: null,
    total_size: 0,
    file_count: 0,
    upload_directories: [],
    default_upload_directory: 'documents',
    updated_at: '2026-08-14T05:00:00Z',
  }
  await page.route('**/api/assets?*', async (route) => {
    const query = new URL(route.request().url()).searchParams.get('query')
    if (query === 'Beta') {
      markBetaStarted?.()
      await betaReleased
      await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Beta 暂不可用' }) })
      return
    }
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ items: [result], total: 1, page: 1, page_size: 20, publication_facets: null }),
    })
  })

  await page.goto('/search?q=Alpha')
  await expect(page.getByText('Alpha 旧结果')).toBeVisible()
  await page.getByLabel('统一检索关键词').fill('Beta')
  await page.getByRole('button', { name: '检索目录' }).click()
  await betaStarted

  await expect(page.locator('.search-summary')).toContainText('与“Beta”相关')
  await expect(page.getByText('Alpha 旧结果')).toBeHidden()
  releaseBeta?.()
  await expect(page.getByRole('alert')).toContainText('Beta 暂不可用')
  await expect(page.getByText('Alpha 旧结果')).toBeHidden()
})

test('目录切换会隔离旧卡片并结束旧目录弹窗', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 1, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  await signInWithMockAccount(page)
  let datasetRequests = 0
  let releaseDatasets: (() => void) | undefined
  const datasetsReleased = new Promise<void>((resolve) => { releaseDatasets = resolve })
  let markDatasetsStarted: (() => void) | undefined
  const datasetsStarted = new Promise<void>((resolve) => { markDatasetsStarted = resolve })
  const literature = {
    id: '53535353-5353-5353-5353-535353535353',
    type: 'literature',
    slug: 'old-literature',
    title: '旧目录文献',
    summary: '不得出现在数据集目录',
    status: 'published',
    visibility: 'lab',
    owner: { id: '54545454-5454-5454-5454-545454545454', name: '测试用户', avatar_url: null },
    details: {
      venue: 'ACL', year: 2026, track: 'Conference Paper', authors: ['Ada Lovelace'],
      source_id: 'old-literature', source_url: 'https://example.com/old', pdf_url: 'https://example.com/old.pdf',
    },
    tags: ['ACL'],
    current_version: null,
    total_size: 0,
    file_count: 0,
    upload_directories: [{ name: 'original', label: '原始文件' }],
    default_upload_directory: 'original',
    updated_at: '2026-08-14T05:00:00Z',
  }
  await page.route('**/api/assets?*', async (route) => {
    const assetType = new URL(route.request().url()).searchParams.get('asset_type')
    if (assetType === 'dataset') {
      datasetRequests += 1
      if (datasetRequests === 1) {
        await route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 20, publication_facets: null }),
        })
        return
      }
      markDatasetsStarted?.()
      await datasetsReleased
      await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '数据集目录暂不可用' }) })
      return
    }
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        items: [literature], total: 1, page: 1, page_size: 20,
        publication_facets: { venues: ['ACL'], years: [2026] },
      }),
    })
  })

  await page.goto('/datasets')
  await expect(page.getByRole('heading', { name: '数据集目录' })).toBeVisible()
  await navigateTo(page, '文献 Literature')
  await page.getByRole('button', { name: '卡片视图' }).click()
  await expect(page.getByText('旧目录文献')).toBeVisible()
  await page.getByRole('button', { name: '上传文件' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.goBack()
  await datasetsStarted

  await expect(page.getByRole('heading', { name: '数据集目录' })).toBeVisible()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByText('旧目录文献')).toBeHidden()
  releaseDatasets?.()
  await expect(page.getByRole('alert')).toContainText('数据集目录暂不可用')
  await expect(page.getByText('旧目录文献')).toBeHidden()
})

test('搜索失败后可重试同一关键词并保留已有结果', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  await signInWithMockAccount(page)
  let requests = 0
  const result = {
    id: '91919191-9191-9191-9191-919191919191',
    type: 'project',
    slug: 'retry-result',
    title: '可重试搜索结果',
    summary: '短暂失败后恢复',
    status: 'active',
    visibility: 'lab',
    owner: { id: '92929292-9292-9292-9292-929292929292', name: '测试用户', avatar_url: null },
    details: {},
    tags: [],
    current_version: null,
    total_size: 0,
    file_count: 0,
    upload_directories: [],
    default_upload_directory: 'documents',
    updated_at: '2026-08-14T02:00:00Z',
  }
  await page.route('**/api/assets?*', async (route) => {
    requests += 1
    if (requests === 2) {
      await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '搜索服务暂不可用' }) })
      return
    }
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ items: [result], total: 1, page: 1, page_size: 20, publication_facets: null }),
    })
  })
  await page.goto('/search?q=LLM')
  await expect(page.getByText('可重试搜索结果')).toBeVisible()

  await page.getByRole('button', { name: '检索目录' }).click()
  await expect(page.getByRole('alert')).toContainText('搜索服务暂不可用')
  await expect(page.getByText('可重试搜索结果')).toBeVisible()

  await page.getByRole('button', { name: '重试' }).click()
  await expect(page.getByRole('alert')).toBeHidden()
  await expect(page.getByText('可重试搜索结果')).toBeVisible()
  expect(requests).toBe(3)
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
  await mockEmptyDashboard(page)
  await signInWithMockAccount(page)
  await page.goto('/import-assets')
  const acceptedTypes = await page.locator('.import-file-picker input').getAttribute('accept')

  expect(acceptedTypes).toContain('.json')
  expect(acceptedTypes).toContain('.csv')
  expect(acceptedTypes).toContain('.yaml')
  await expect(page.getByLabel('导入数据内容')).toBeVisible()
})

test('批量导入支持粘贴 YAML 并定位结构化校验错误', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  await page.route('**/api/assets/import/yaml', async (route) => {
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({ created: [{ title: 'YAML 粘贴资产' }] }),
    })
  })
  await page.route('**/api/assets/import', async (route) => {
    await route.fulfill({
      status: 422,
      contentType: 'application/json',
      body: JSON.stringify({
        detail: [{
          type: 'enum',
          loc: ['body', 'assets', 1, 'type'],
          msg: 'Input should be a valid asset type',
          input: 'unknown',
        }],
      }),
    })
  })
  await signInWithMockAccount(page)
  await page.goto('/import-assets')

  await page.getByRole('button', { name: 'YAML' }).click()
  await page.getByLabel('导入数据内容').fill([
    'assets:',
    '  - type: dataset',
    '    slug: yaml-pasted-asset',
    '    title: YAML 粘贴资产',
  ].join('\n'))
  await page.getByRole('button', { name: '验证并导入' }).click()
  await expect(page.getByText('已创建 1 条资产')).toBeVisible()
  await expect(page.getByText('YAML 粘贴资产', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'JSON' }).click()
  await page.getByLabel('导入数据内容').fill(JSON.stringify([
    { type: 'dataset', slug: 'valid-record', title: 'Valid Record' },
    { type: 'unknown', slug: 'invalid-record', title: 'Invalid Record' },
  ]))
  await page.getByRole('button', { name: '验证并导入' }).click()
  await expect(page.getByRole('alert')).toContainText('第 2 条 · type：Input should be a valid asset type')
  await expect(page.getByRole('alert')).not.toContainText('请求失败（422）')
})

test('CSV 导入支持 BOM、转义引号和跨行字段', async ({ page }) => {
  await mockEmptyDashboard(page)
  await signInWithMockAccount(page)
  await page.goto('/import-assets')
  await page.locator('.import-file-picker input').setInputFiles({
    name: 'assets.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('\uFEFFtype,slug,title,summary\nliterature,csv-reader,"A ""quoted"" title","first line\nsecond line"'),
  })

  const parsed = JSON.parse(await page.getByLabel('导入数据内容').inputValue()) as Array<Record<string, unknown>>
  expect(parsed).toMatchObject([{
    type: 'literature',
    slug: 'csv-reader',
    title: 'A "quoted" title',
    summary: 'first line\nsecond line',
  }])

  await page.locator('.import-file-picker input').setInputFiles({
    name: 'invalid-details.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('type,slug,title,details\ndataset,invalid-details,Invalid Details,"[]"'),
  })
  await expect(page.getByText('CSV 第 2 行 details 必须是 JSON 对象。')).toBeVisible()
})

test('较慢的旧文件读取不会覆盖最新选择', async ({ page }) => {
  await mockEmptyDashboard(page)
  await signInWithMockAccount(page)
  await page.goto('/import-assets')
  await page.evaluate(() => {
    const pendingReads = new Map<string, (content: string) => void>()
    Object.defineProperty(window, '__resolveImportFile', {
      configurable: true,
      value: (name: string, content: string) => pendingReads.get(name)?.(content),
    })
    File.prototype.text = function () {
      return new Promise<string>((resolve) => pendingReads.set(this.name, resolve))
    }
  })
  const fileInput = page.locator('.import-file-picker input')

  await fileInput.setInputFiles({ name: 'older.json', mimeType: 'application/json', buffer: Buffer.from('{}') })
  await expect(page.getByRole('button', { name: '正在读取文件' })).toBeDisabled()
  await fileInput.setInputFiles({ name: 'latest.yaml', mimeType: 'text/yaml', buffer: Buffer.from('') })
  await page.evaluate(() => {
    const resolveImportFile = (window as unknown as { __resolveImportFile: (name: string, content: string) => void }).__resolveImportFile
    resolveImportFile('latest.yaml', 'assets:\n  - type: dataset\n    slug: latest\n    title: Latest')
  })

  await expect(page.getByLabel('导入数据内容')).toHaveValue(/slug: latest/)
  await expect(page.getByRole('button', { name: 'YAML' })).toHaveAttribute('aria-pressed', 'true')
  await page.evaluate(() => {
    const resolveImportFile = (window as unknown as { __resolveImportFile: (name: string, content: string) => void }).__resolveImportFile
    resolveImportFile('older.json', '[{"type":"dataset","slug":"older","title":"Older"}]')
  })
  await expect(page.getByLabel('导入数据内容')).toHaveValue(/slug: latest/)
  await expect(page.getByLabel('导入数据内容')).not.toHaveValue(/slug":"older/)
})

test('搜索与品牌文件控件提供稳定的可访问名称', async ({ page }) => {
  await mockEmptyDashboard(page)
  await mockEmptyCatalogue(page)
  await mockEmptySettingsCollections(page)
  await signInWithMockAccount(page)
  await page.goto('/literature')
  await expect(page.getByRole('textbox', { name: '搜索文献' })).toBeVisible()

  await page.goto('/search')
  await expect(page.getByRole('textbox', { name: '统一检索关键词' })).toBeVisible()

  await page.goto('/settings')
  await expect(page.getByLabel('选择实例 Logo 图片')).toHaveCount(1)
})

test('待认领文件必须搜索并明确选择目标资产', async ({ page }) => {
  await mockEmptyDashboard(page)
  await signInWithMockAccount(page)
  await page.route('**/api/archive/unclaimed?*', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ items: [{
        id: '11111111-1111-1111-1111-111111111111',
        relative_path: 'incoming/unassigned.pdf',
        file_name: 'unassigned.pdf',
        file_kind: 'document',
        mime_type: 'application/pdf',
        file_size: 2048,
        modified_at: null,
        first_seen_at: '2026-08-13T04:00:00Z',
        last_seen_at: '2026-08-13T04:00:00Z',
      }], total: 1, page: 1, page_size: 50 }),
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
  await page.getByRole('button', { name: '认领文件：unassigned.pdf' }).click()
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

test('待认领文件加载期间禁止重复刷新', async ({ page }) => {
  await signIn(page)
  let releaseRequest: (() => void) | undefined
  const requestReleased = new Promise<void>((resolve) => { releaseRequest = resolve })
  await page.route('**/api/archive/unclaimed?*', async (route) => {
    await requestReleased
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ items: [{
        id: '99999999-0000-0000-0000-000000000000',
        relative_path: 'unclaimed/latest.pdf',
        file_name: 'latest.pdf',
        file_kind: 'document',
        mime_type: 'application/pdf',
        file_size: 128,
        modified_at: '2026-08-13T06:00:00Z',
      }], total: 1, page: 1, page_size: 50 }),
    })
  })

  const navigation = page.goto('/unclaimed-files')
  const refresh = page.getByRole('button', { name: '刷新列表' })
  await expect(refresh).toBeDisabled()
  releaseRequest?.()
  await navigation
  await expect(page.getByText('latest.pdf', { exact: true })).toBeVisible()
  await expect(refresh).toBeEnabled()
})

test('低高度移动端认领弹窗保持完整可滚动', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 500 })
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  await page.route('**/api/archive/unclaimed?*', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ items: [{
        id: '12121212-3434-5656-7878-909090909090',
        relative_path: 'incoming/a-very-long-unclaimed-publication-file-name.pdf',
        file_name: 'a-very-long-unclaimed-publication-file-name.pdf',
        file_kind: 'document',
        mime_type: 'application/pdf',
        file_size: 4096,
        modified_at: '2026-08-14T02:00:00Z',
        first_seen_at: '2026-08-14T02:00:00Z',
        last_seen_at: '2026-08-14T02:00:00Z',
      }], total: 1, page: 1, page_size: 50 }),
    })
  })
  await page.route('**/api/assets/choices?*', async (route) => {
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: '候选资产暂时不可用，请稍后重试' }),
    })
  })
  await signInWithMockAccount(page)
  await page.goto('/unclaimed-files')
  await page.getByRole('button', { name: '认领文件：a-very-long-unclaimed-publication-file-name.pdf' }).click()

  const dialog = page.getByRole('dialog', { name: /认领/ })
  const dialogBounds = await dialog.boundingBox()
  expect(dialogBounds).not.toBeNull()
  expect(dialogBounds!.y).toBeGreaterThanOrEqual(0)
  expect(dialogBounds!.y + dialogBounds!.height).toBeLessThanOrEqual(500)
  await expect(dialog.getByRole('alert')).toContainText('候选资产暂时不可用')
  await dialog.getByRole('button', { name: '取消' }).scrollIntoViewIfNeeded()
  await expect(dialog.getByRole('button', { name: '取消' })).toBeInViewport()
  await dialog.getByRole('button', { name: '取消' }).click()
  await expect(dialog).toBeHidden()
})

test('并发恢复已归档资产时各行保持独立忙碌状态', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  const archivedAssets = ['第一项归档资产', '第二项归档资产'].map((title, index) => ({
    id: `${index + 1}1111111-2222-3333-4444-555555555555`,
    type: 'dataset',
    slug: `archived-${index + 1}`,
    title,
    summary: `归档资产 ${index + 1}`,
    status: 'archived',
    visibility: 'lab',
    owner: { id: '99999999-8888-7777-6666-555555555555', name: '测试用户', avatar_url: null },
    details: {},
    tags: [],
    current_version: null,
    total_size: 0,
    file_count: 0,
    upload_directories: [],
    default_upload_directory: 'raw',
    updated_at: '2026-08-14T02:00:00Z',
  }))
  await page.route('**/api/assets/archived?*', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ items: archivedAssets, total: archivedAssets.length, page: 1, page_size: 20 }),
    })
  })
  const restoreReleases = new Map<string, () => void>()
  const restoreCounts = new Map<string, number>()
  await page.route('**/api/assets/*/restore', async (route) => {
    const assetId = route.request().url().split('/').at(-2)!
    restoreCounts.set(assetId, (restoreCounts.get(assetId) ?? 0) + 1)
    await new Promise<void>((resolve) => restoreReleases.set(assetId, resolve))
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(archivedAssets.find((asset) => asset.id === assetId)) })
  })
  await signInWithMockAccount(page)
  await page.goto('/archived-assets')

  const firstRow = page.locator('.archived-row').filter({ hasText: archivedAssets[0].title })
  const secondRow = page.locator('.archived-row').filter({ hasText: archivedAssets[1].title })
  await firstRow.getByRole('button', { name: '恢复资产：第一项归档资产' }).click()
  await secondRow.getByRole('button', { name: '恢复资产：第二项归档资产' }).click()
  await expect(firstRow.getByRole('button', { name: '正在恢复资产：第一项归档资产' })).toBeDisabled()
  await expect(secondRow.getByRole('button', { name: '正在恢复资产：第二项归档资产' })).toBeDisabled()

  restoreReleases.get(archivedAssets[0].id)?.()
  await expect(firstRow).toBeHidden()
  await expect(secondRow.getByRole('button', { name: '正在恢复资产：第二项归档资产' })).toBeDisabled()
  expect(restoreCounts.get(archivedAssets[1].id)).toBe(1)

  restoreReleases.get(archivedAssets[1].id)?.()
  await expect(secondRow).toBeHidden()
})

test('已归档资产分页归一化并在末页恢复后回退', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  const archivedAsset = {
    id: '33333333-2222-3333-4444-555555555555',
    type: 'dataset',
    slug: 'last-archived',
    title: '末页唯一归档资产',
    summary: '恢复后应回到第一页',
    status: 'archived',
    visibility: 'lab',
    owner: { id: '99999999-8888-7777-6666-555555555555', name: '测试用户', avatar_url: null },
    details: {},
    tags: [],
    current_version: null,
    total_size: 0,
    file_count: 0,
    upload_directories: [],
    default_upload_directory: 'raw',
    updated_at: '2026-08-14T02:00:00Z',
  }
  await page.route('**/api/assets/archived?*', async (route) => {
    const requestedPage = new URL(route.request().url()).searchParams.get('page')
    if (requestedPage === '2') {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ items: [archivedAsset], total: 21, page: 2, page_size: 20 }),
      })
      return
    }
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ items: [], total: 20, page: 1, page_size: 20 }),
    })
  })
  await page.route('**/api/assets/*/restore', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...archivedAsset, status: 'active' }) })
  })
  await signInWithMockAccount(page)
  await page.goto('/archived-assets?page=0')
  await expect(page).toHaveURL('/archived-assets')

  await page.goto('/archived-assets?page=2')
  await expect(page.getByText('末页唯一归档资产')).toBeVisible()
  await expect(page.getByText('第 2 / 2 页')).toBeVisible()
  await page.getByRole('button', { name: '恢复资产：末页唯一归档资产' }).click()

  await expect(page).toHaveURL('/archived-assets')
  await expect(page.getByText('末页唯一归档资产')).toBeHidden()
})

test('归档扫描完成后摘要刷新失败仍保留已有健康数据', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  await signInWithMockAccount(page)
  let healthRequests = 0
  const health = {
    storage_available: true,
    latest_scan: null,
    recent_scans: [],
    indexed_files: 27,
    healthy_files: 25,
    missing_files: 2,
    unclaimed_files: 3,
  }
  await page.route('**/api/archive/health', async (route) => {
    healthRequests += 1
    if (healthRequests === 1) {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(health) })
      return
    }
    await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '摘要服务暂不可用' }) })
  })
  await page.route('**/api/archive/scans', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        id: '77777777-7777-7777-7777-777777777777',
        status: 'completed',
        source: 'storage-root',
        files_discovered: 30,
        files_indexed: 27,
        files_missing: 2,
        files_unclaimed: 3,
        files_skipped: 0,
        message: '扫描完成',
        started_at: '2026-08-14T01:00:00Z',
        completed_at: '2026-08-14T01:01:00Z',
      }),
    })
  })

  await page.goto('/archive-health')
  await expect(page.getByText('25', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '运行扫描' }).click()

  await expect(page.getByRole('alert')).toContainText('扫描已完成，但暂时无法刷新健康摘要')
  await expect(page.getByText('25', { exact: true })).toBeVisible()
  await expect(page.getByText('归档服务暂不可用')).toBeHidden()
  await expect(page.getByRole('button', { name: '运行扫描' })).toBeEnabled()
})

test('扫描记录用文本呈现失败状态和原因', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 500 })
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  const failedScan = {
    id: '88888888-8888-8888-8888-888888888888',
    status: 'failed',
    source: 'storage-root',
    files_discovered: 0,
    files_indexed: 0,
    files_missing: 0,
    files_unclaimed: 0,
    files_skipped: 0,
    message: '存储根不可用，未执行扫描。',
    started_at: '2026-08-14T01:00:00Z',
    completed_at: '2026-08-14T01:00:01Z',
  }
  await page.route('**/api/archive/health', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        storage_available: false,
        latest_scan: failedScan,
        recent_scans: [failedScan],
        indexed_files: 0,
        healthy_files: 0,
        missing_files: 0,
        unclaimed_files: 0,
      }),
    })
  })
  await signInWithMockAccount(page)
  await page.goto('/archive-health')

  const scanRow = page.locator('.scan-row')
  await expect(scanRow.getByText('失败', { exact: true })).toBeVisible()
  await expect(scanRow.getByText('存储根不可用，未执行扫描。')).toBeVisible()
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

test('操作日志连续筛选只保留当前请求的结果和加载状态', async ({ page }) => {
  await signIn(page)
  const releaseRequests = new Map<string, () => void>()
  const requestStarted = new Map<string, Promise<void>>()
  const markRequestStarted = new Map<string, () => void>()
  for (const action of ['archived', 'created']) {
    requestStarted.set(action, new Promise<void>((resolve) => markRequestStarted.set(action, resolve)))
  }

  await page.route('**/api/dashboard/activities?*', async (route) => {
    const action = new URL(route.request().url()).searchParams.get('action') ?? ''
    if (action) {
      markRequestStarted.get(action)?.()
      await new Promise<void>((resolve) => releaseRequests.set(action, resolve))
    }
    const label = action === 'archived' ? '归档资产' : action === 'created' ? '登记资产' : '全部操作'
    try {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          items: [{
            id: `${action || 'all'}-activity`,
            asset_id: null,
            asset_title: null,
            asset_type: null,
            actor_name: '测试用户',
            credential_name: null,
            action: action || 'all',
            action_label: label,
            description: `${label}请求结果`,
            created_at: '2026-08-13T06:00:00Z',
          }],
          facets: [
            { value: 'archived', label: '归档资产', count: 1 },
            { value: 'created', label: '登记资产', count: 1 },
          ],
          total: 1,
          page: 1,
          page_size: 30,
        }),
      })
    } catch {
      // The application intentionally aborts superseded requests.
    }
  })

  await page.goto('/activity-log')
  const filter = page.getByLabel('操作类型')
  await expect(page.getByText('全部操作请求结果')).toBeVisible()

  await filter.selectOption('archived')
  await requestStarted.get('archived')
  await filter.selectOption('created')
  await requestStarted.get('created')
  await expect(page.getByRole('status')).toContainText('正在读取操作日志')

  releaseRequests.get('archived')?.()
  await expect(page.getByRole('status')).toContainText('正在读取操作日志')
  await expect(page.getByText('归档资产请求结果')).toBeHidden()

  releaseRequests.get('created')?.()
  await expect(page.getByText('登记资产请求结果')).toBeVisible()
  await expect(page.getByText('归档资产请求结果')).toBeHidden()
  await expect(page.getByRole('status')).toBeHidden()
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
  await page.getByLabel('摘要（必填）').fill('用于验证完整期刊引用元数据。')
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

test('文献登记冲突保留表单并在固定操作区显示错误', async ({ page }) => {
  await signIn(page)
  await page.route('**/api/assets', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue()
      return
    }
    await route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({ detail: '该出版物已经收录，请更新现有记录。' }),
    })
  })

  await page.goto('/literature')
  await page.getByRole('button', { name: '登记文献' }).click()
  await page.getByLabel('标题').fill('重复文献')
  await page.getByLabel('资产标识（slug）').fill('duplicate-literature')
  await page.getByLabel('摘要（必填）').fill('用于验证重复文献冲突。')
  await page.getByLabel('来源或期刊').fill('ICLR')
  await page.getByLabel('文献类别').fill('Conference Poster')
  await page.getByLabel('作者（逗号分隔）').fill('Ada Lovelace')
  await page.getByLabel('官方来源标识').fill('duplicate-source')
  await page.getByLabel('官方页面 URL').fill('https://example.com/paper')
  await page.getByLabel('官方 PDF URL').fill('https://example.com/paper.pdf')
  await page.getByLabel('期刊名称').fill('ICLR')
  await page.getByRole('button', { name: '确认登记' }).click()

  const dialog = page.getByRole('dialog', { name: '登记文献' })
  const error = dialog.getByRole('alert')
  const submit = dialog.getByRole('button', { name: '确认登记' })
  await expect(error).toHaveText('该出版物已经收录，请更新现有记录。')
  await expect(page.getByLabel('标题')).toHaveValue('重复文献')
  await expect(submit).toBeInViewport()
  await expect(error).toBeInViewport()

  page.once('dialog', async (confirmation) => confirmation.accept())

  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
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

test('详情切换后不会被上一项的延迟引用覆盖', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  await signInWithMockAccount(page)
  const publicationId = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
  const datasetId = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
  let releaseCitation: (() => void) | undefined
  let markCitationStarted: (() => void) | undefined
  let releaseFileTicket: (() => void) | undefined
  let markFileTicketStarted: (() => void) | undefined
  const citationReleased = new Promise<void>((resolve) => { releaseCitation = resolve })
  const citationStarted = new Promise<void>((resolve) => { markCitationStarted = resolve })
  const fileTicketReleased = new Promise<void>((resolve) => { releaseFileTicket = resolve })
  const fileTicketStarted = new Promise<void>((resolve) => { markFileTicketStarted = resolve })
  const owner = { id: 'cccccccc-cccc-cccc-cccc-cccccccccccc', name: '测试用户', avatar_url: null }
  const fileId = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee'

  await page.route(`**/api/assets/${publicationId}`, async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        id: publicationId,
        type: 'literature',
        slug: 'delayed-citation',
        title: '延迟引用文献',
        summary: '用于验证引用与详情加载相互独立',
        status: 'published',
        visibility: 'lab',
        owner,
        details: {
          venue: 'ACL',
          year: 2026,
          track: 'Conference Paper',
          authors: ['Ada Lovelace'],
          source_id: 'acl-2026-delayed',
          source_url: 'https://example.com/source',
          pdf_url: 'https://example.com/paper.pdf',
          abstract: '详情应当先于引用内容出现。',
        },
        tags: ['ACL'],
        current_version: 'v1',
        total_size: 2048,
        file_count: 1,
        upload_directories: [],
        default_upload_directory: 'source',
        updated_at: '2026-08-13T06:00:00Z',
        versions: [],
        files: [{
          id: fileId,
          relative_path: 'literature/delayed-citation/source/paper.pdf',
          file_name: 'paper.pdf',
          file_kind: 'document',
          mime_type: 'application/pdf',
          file_size: 2048,
          health_status: 'healthy',
          modified_at: '2026-08-13T06:00:00Z',
        }],
        related_assets: [{
          relation_id: 'dddddddd-dddd-dddd-dddd-dddddddddddd',
          id: datasetId,
          type: 'dataset',
          slug: 'current-dataset',
          title: '当前数据集',
          relation_type: 'supports',
        }],
        recent_activities: [],
      }),
    })
  })
  await page.route(`**/api/assets/${publicationId}/citation/bibtex`, async (route) => {
    markCitationStarted?.()
    await citationReleased
    try {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          citation_key: 'stale2026citation',
          filename: 'stale2026citation.bib',
          bibtex: '@article{stale2026citation, title={不应出现的旧引用}}',
        }),
      })
    } catch {
      // The application intentionally aborts citation loading after navigation.
    }
  })
  await page.route(`**/api/files/${fileId}/tickets`, async (route) => {
    markFileTicketStarted?.()
    await fileTicketReleased
    try {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          content_url: `/api/files/${fileId}/content?grant=stale`,
          expires_at: '2026-08-13T06:10:00Z',
        }),
      })
    } catch {
      // The application intentionally aborts file access after navigation.
    }
  })
  await page.route(`**/api/assets/${datasetId}`, async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        id: datasetId,
        type: 'dataset',
        slug: 'current-dataset',
        title: '当前数据集',
        summary: '这是切换后应稳定显示的资产。',
        status: 'active',
        visibility: 'lab',
        owner,
        details: { format: 'CSV' },
        tags: ['dataset'],
        current_version: 'v2',
        total_size: 0,
        file_count: 0,
        upload_directories: [],
        default_upload_directory: 'raw',
        updated_at: '2026-08-13T06:05:00Z',
        versions: [],
        files: [],
        related_assets: [],
        recent_activities: [],
      }),
    })
  })

  await page.goto(`/assets/${publicationId}?returnTo=/literature`)
  await expect(page.getByRole('heading', { name: '延迟引用文献' })).toBeVisible()
  await citationStarted
  await expect(page.getByRole('status')).toContainText('正在生成引用')
  await page.getByTitle('浏览器预览').click()
  await fileTicketStarted

  await page.getByRole('link', { name: /当前数据集/ }).click()
  await expect(page.getByRole('heading', { name: '当前数据集' })).toBeVisible()
  await expect(page.getByText('这是切换后应稳定显示的资产。')).toBeVisible()
  await expect(page.getByRole('heading', { name: '出版物引用' })).toBeHidden()

  releaseFileTicket?.()
  await expect(page.getByRole('dialog', { name: '预览 paper.pdf' })).toBeHidden()
  releaseCitation?.()
  await expect(page.getByText('不应出现的旧引用')).toBeHidden()
  await page.getByRole('button', { name: '编辑' }).click()
  await page.getByRole('dialog', { name: '编辑资产' }).getByLabel('标题').fill('不应带回上一项的草稿')
  page.once('dialog', async (dialog) => dialog.accept())
  await page.goBack()
  await expect(page.getByRole('heading', { name: '延迟引用文献' })).toBeVisible()
  await expect(page.getByRole('dialog', { name: '编辑资产' })).toBeHidden()
  await expect(page.getByText('不应带回上一项的草稿')).toBeHidden()
  await expect(page.getByText('不应出现的旧引用')).toBeVisible()
})

test('归档请求完成后不会导航或污染新资产', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  await signInWithMockAccount(page)
  const firstId = '10101010-1010-1010-1010-101010101010'
  const secondId = '20202020-2020-2020-2020-202020202020'
  const owner = { id: '30303030-3030-3030-3030-303030303030', name: '测试用户', avatar_url: null }
  const detail = (id: string, type: 'literature' | 'dataset', title: string, relatedAssets: unknown[]) => ({
    id,
    type,
    slug: `${type}-${id.slice(0, 4)}`,
    title,
    summary: `${title}摘要`,
    status: 'active',
    visibility: 'lab',
    owner,
    details: {},
    tags: [],
    current_version: null,
    total_size: 0,
    file_count: 0,
    upload_directories: [],
    default_upload_directory: 'raw',
    updated_at: '2026-08-14T02:00:00Z',
    versions: [],
    files: [],
    related_assets: relatedAssets,
    recent_activities: [],
  })
  const firstAsset = detail(firstId, 'literature', '待归档文献', [{
    relation_id: '40404040-4040-4040-4040-404040404040',
    id: secondId,
    type: 'dataset',
    slug: 'current-dataset',
    title: '当前数据集',
    relation_type: 'supports',
  }])
  const secondAsset = detail(secondId, 'dataset', '当前数据集', [])
  await page.route(`**/api/assets/${firstId}`, async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(firstAsset) })
  })
  await page.route(`**/api/assets/${secondId}`, async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(secondAsset) })
  })
  let releaseArchive: (() => void) | undefined
  let markArchiveStarted: (() => void) | undefined
  const archiveReleased = new Promise<void>((resolve) => { releaseArchive = resolve })
  const archiveStarted = new Promise<void>((resolve) => { markArchiveStarted = resolve })
  await page.route(`**/api/assets/${firstId}/archive`, async (route) => {
    markArchiveStarted?.()
    await archiveReleased
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...firstAsset, status: 'archived' }) })
  })
  page.on('dialog', (dialog) => dialog.accept())
  await page.goto(`/assets/${firstId}`)
  await page.getByRole('button', { name: '归档' }).click()
  await archiveStarted
  await page.getByRole('link', { name: /当前数据集/ }).click()

  await expect(page.getByRole('heading', { name: '当前数据集' })).toBeVisible()
  await expect(page.getByRole('button', { name: '归档' })).toBeEnabled()
  releaseArchive?.()
  await expect(page).toHaveURL(`/assets/${secondId}`)
  await expect(page.getByRole('heading', { name: '当前数据集' })).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('文件预览可以用键盘进入 iframe 内容', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 0, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  await signInWithMockAccount(page)
  const assetId = '50505050-5050-5050-5050-505050505050'
  const fileId = '60606060-6060-6060-6060-606060606060'
  await page.route(`**/api/assets/${assetId}`, async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        id: assetId,
        type: 'dataset',
        slug: 'keyboard-preview',
        title: '键盘预览数据',
        summary: '验证预览焦点',
        status: 'active',
        visibility: 'lab',
        owner: { id: '70707070-7070-7070-7070-707070707070', name: '测试用户', avatar_url: null },
        details: {},
        tags: [],
        current_version: null,
        total_size: 128,
        file_count: 1,
        upload_directories: [],
        default_upload_directory: 'raw',
        updated_at: '2026-08-14T02:00:00Z',
        versions: [],
        files: [{
          id: fileId,
          relative_path: 'dataset/keyboard-preview/raw/readme.txt',
          file_name: 'readme.txt',
          file_kind: 'document',
          mime_type: 'text/plain',
          file_size: 128,
          health_status: 'healthy',
          modified_at: '2026-08-14T02:00:00Z',
        }],
        related_assets: [],
        recent_activities: [],
      }),
    })
  })
  await page.route(`**/api/files/${fileId}/tickets`, async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ content_url: 'about:blank', expires_at: '2026-08-14T03:00:00Z' }),
    })
  })
  await page.goto(`/assets/${assetId}`)
  await page.getByTitle('浏览器预览').click()
  await expect(page.getByRole('dialog', { name: '预览 readme.txt' })).toBeVisible()
  await expect(page.getByRole('button', { name: '关闭预览' })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(page.locator('.preview-dialog iframe')).toBeFocused()
})

test('详情操作失败不会替换已加载的资产内容', async ({ page }) => {
  await signIn(page)
  await page.goto('/literature?view=grid')
  await page.getByRole('link', { name: /查看详情/ }).first().click()
  const title = await page.getByRole('heading', { level: 1 }).textContent()
  await page.route('**/api/assets/*/archive', async (route) => {
    await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: '归档冲突，请稍后重试' }) })
  })

  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '归档' }).click()

  await expect(page.getByRole('alert')).toContainText('归档冲突，请稍后重试')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(title ?? '')
  await expect(page.getByRole('heading', { name: '归档概要' })).toBeVisible()
})

test('详情编辑以写接口响应更新页面且不依赖二次读取', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 1, literature: 0, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  await signInWithMockAccount(page)
  const assetId = '71717171-7171-7171-7171-717171717171'
  const asset = {
    id: assetId,
    type: 'dataset',
    slug: 'direct-update',
    title: '更新前标题',
    summary: '用于验证写接口响应直接更新页面。',
    status: 'active',
    visibility: 'lab',
    owner: { id: '72727272-7272-7272-7272-727272727272', name: '测试用户', avatar_url: null },
    details: {},
    tags: [],
    current_version: null,
    total_size: 0,
    file_count: 0,
    upload_directories: [],
    default_upload_directory: 'raw',
    updated_at: '2026-08-14T03:00:00Z',
    versions: [],
    files: [],
    related_assets: [],
    recent_activities: [],
  }
  let detailReads = 0
  await page.route(`**/api/assets/${assetId}`, async (route) => {
    if (route.request().method() === 'PATCH') {
      const payload = route.request().postDataJSON() as { title: string }
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ ...asset, title: payload.title, updated_at: '2026-08-14T03:01:00Z' }),
      })
      return
    }
    detailReads += 1
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(asset) })
  })

  await page.goto(`/assets/${assetId}`)
  await page.getByRole('button', { name: '编辑' }).click()
  const dialog = page.getByRole('dialog', { name: '编辑资产' })
  await dialog.getByLabel('标题').fill('更新后标题')
  await dialog.getByRole('button', { name: '保存修改' }).click()

  await expect(dialog).toBeHidden()
  await expect(page.getByRole('heading', { name: '更新后标题' })).toBeVisible()
  expect(detailReads).toBe(1)
})

test('目录 BibTeX 复制以最后一次选择为准', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { paper: 0, dataset: 0, literature: 2, project: 0, model: 0 },
        total_storage_bytes: 0,
        healthy_files: 0,
        missing_files: 0,
        recent_assets: [],
        recent_activities: [],
        popular_tags: [],
      }),
    })
  })
  await signInWithMockAccount(page)
  const owner = { id: '73737373-7373-7373-7373-737373737373', name: '测试用户', avatar_url: null }
  const publication = (id: string, title: string) => ({
    id,
    type: 'literature',
    slug: title.toLowerCase().replace(' ', '-'),
    title,
    summary: `${title} 摘要`,
    status: 'published',
    visibility: 'lab',
    owner,
    details: {
      venue: 'ACL', year: 2026, track: 'Conference Paper', authors: ['Ada Lovelace'],
      source_id: id, source_url: `https://example.com/${id}`, pdf_url: `https://example.com/${id}.pdf`,
    },
    tags: ['ACL'],
    current_version: null,
    total_size: 0,
    file_count: 0,
    upload_directories: [],
    default_upload_directory: 'source',
    updated_at: '2026-08-14T03:00:00Z',
  })
  const firstId = '74747474-7474-7474-7474-747474747474'
  const secondId = '75757575-7575-7575-7575-757575757575'
  const publications = [publication(firstId, 'First Paper'), publication(secondId, 'Second Paper')]
  await page.route('**/api/assets?*', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ items: publications, total: 2, page: 1, page_size: 20, publication_facets: { venues: ['ACL'], years: [2026] } }),
    })
  })
  let releaseFirst: (() => void) | undefined
  const firstReleased = new Promise<void>((resolve) => { releaseFirst = resolve })
  await page.route(`**/api/assets/${firstId}/citation/bibtex`, async (route) => {
    await firstReleased
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ citation_key: 'first', filename: 'first.bib', bibtex: '@article{first}' }) })
  })
  await page.route(`**/api/assets/${secondId}/citation/bibtex`, async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ citation_key: 'second', filename: 'second.bib', bibtex: '@article{second}' }) })
  })

  await page.goto('/literature?view=grid')
  const firstCard = page.locator('.catalogue-card').filter({ hasText: 'First Paper' })
  const secondCard = page.locator('.catalogue-card').filter({ hasText: 'Second Paper' })
  await firstCard.getByRole('button', { name: 'BibTeX' }).click()
  await secondCard.getByRole('button', { name: 'BibTeX' }).click()
  await expect(secondCard.getByRole('button', { name: '已复制' })).toBeVisible()
  releaseFirst?.()
  await expect(secondCard.getByRole('button', { name: '已复制' })).toBeVisible()
  await expect(firstCard.getByRole('button', { name: '已复制' })).toHaveCount(0)
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

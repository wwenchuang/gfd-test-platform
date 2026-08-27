import { expect, test } from 'playwright/test'

import { startFavoritesAcceptance } from './fixtures/api-testing/favorites-target.mjs'

let acceptance

test.beforeAll(async () => {
  acceptance = await startFavoritesAcceptance()
})

test.afterAll(async () => {
  await acceptance?.close()
})

test('我的收藏三接口完成导入、AI 设计、调试、基线回归和报告闭环', async ({ page }, testInfo) => {
  const secret = acceptance.secret
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${acceptance.platformUrl}/task-manager.html`)
  await page.locator('#username').fill('admin')
  await page.locator('#password').fill('sonic2026')
  await page.getByRole('button', { name: '登 录' }).click()
  await page.locator('.api-test-link').click()

  await page.getByRole('link', { name: '接口资产' }).click()
  await page.getByRole('button', { name: '新建平台项目' }).click()
  await page.getByLabel('项目名称').fill('3D 我的收藏')
  await page.getByRole('button', { name: '创建', exact: true }).click()
  await expect(page.getByRole('combobox', { name: '平台项目', exact: true })).toHaveValue(/.+/)
  await page.getByText('高级导入：接口定义文件（JSON）', { exact: true }).click()
  await page.locator('input[type="file"]').setInputFiles(acceptance.openApiPath)
  await page.getByRole('button', { name: '读取并比较' }).click()
  await page.getByRole('button', { name: '确认保存接口' }).click()
  await expect(page.getByText(/接口版本 v\d+ 已保存/)).toBeVisible()

  await page.getByRole('link', { name: '环境配置' }).click()
  await page.getByRole('button', { name: '新建环境' }).click()
  await page.getByLabel('环境名称').fill('生产环境（腾讯云）')
  await page.getByTitle('添加变量').click()
  await page.getByLabel('变量名', { exact: true }).fill('Biz')
  await page.getByLabel('变量值', { exact: true }).fill('ZXB')
  await page.getByLabel('敏感变量值').fill(secret)
  await page.getByRole('button', { name: '保存环境' }).click()
  await expect(page.getByText(/环境 .* 已保存/)).toBeVisible()
  await expect(page.locator('body')).not.toContainText(secret)

  await page.getByRole('link', { name: '工作台' }).click()
  await page.getByTestId('context-project').selectOption({ label: '3D 我的收藏' })
  await page.getByTestId('context-source').selectOption({ index: 1 })
  await page.getByTestId('context-environment').selectOption({ index: 1 })
  await page.getByRole('button', { name: '保存测试范围' }).click()
  await expect(page.getByText('范围已保存')).toBeVisible()
  for (const summary of ['查询我的收藏', '添加收藏', '取消收藏']) {
    await page.getByTestId('endpoint-search').fill(summary)
    await page.getByRole('button', { name: new RegExp(summary) }).locator('..').getByRole('checkbox').check()
  }
  await page.getByTestId('endpoint-search').fill('')
  await page.getByTestId('save-task').click()
  await expect(page.getByText('已保存 3 个接口')).toBeVisible()
  await page.reload()
  await expect(page.getByText('已保存 3 个接口')).toBeVisible()
  await expect(page.getByTestId('selected-tab')).toHaveClass(/active/)
  for (const summary of ['查询我的收藏', '添加收藏', '取消收藏']) {
    await page.getByTestId('endpoint-search').fill(summary)
    await expect(page.getByRole('button', { name: new RegExp(summary) })).toBeVisible()
  }
  await page.getByTestId('endpoint-search').fill('')
  await page.getByRole('button', { name: '生成测试用例' }).click()
  await expect(page.getByText('已完成', { exact: true })).toBeVisible()
  await expect(page.locator('.design-workspace')).toBeVisible()
  await assertNoHorizontalOverflow(page)
  await page.screenshot({ path: testInfo.outputPath('workbench-desktop.png'), fullPage: true })
  await page.getByTitle('收起 AI 助手').click()
  await expect(page.locator('.ai-assistant')).toHaveClass(/collapsed/)
  await page.getByTitle('展开 AI 助手').click()

  await page.getByRole('link', { name: '用例管理' }).click()
  await expect(page.getByTestId('cases-page')).toBeVisible()
  await expect(page.getByTestId('case-generation-status')).toContainText('已完成')
  await page.getByTestId('case-generation-results').click()
  await expect(page.locator('[data-testid^="case-version-"]').first()).toBeVisible()
  for (const view of ['regular', 'debugged', 'baseline', 'task', 'orchestrated', 'one-time', 'candidate', 'all']) {
    await page.getByTestId(`case-work-view-${view}`).click()
  }
  await page.getByTestId('case-list-search').fill('查询我的收藏')
  await expect(page.getByText('查询我的收藏', { exact: false }).first()).toBeVisible()
  await page.getByTestId('case-list-search').fill('')
  await page.getByTestId('open-case-endpoint-picker').click()
  await page.getByTestId('case-endpoint-search').fill('查询我的收藏')
  await expect(page.locator('[data-testid^="case-endpoint-"]').filter({ hasText: '查询我的收藏' }).first()).toBeVisible()
  await page.getByTitle('关闭接口选择').click()
  await page.getByRole('link', { name: '工作台' }).click()
  await expect(page.locator('.design-workspace')).toBeVisible()

  await page.setViewportSize({ width: 390, height: 844 })
  await assertNoHorizontalOverflow(page)
  await page.screenshot({ path: testInfo.outputPath('workbench-mobile.png'), fullPage: true })
  await page.setViewportSize({ width: 1440, height: 900 })

  await page.getByTestId('endpoint-search').fill('查询我的收藏')
  await page.locator('.endpoint-tree').getByRole('button', { name: /查询我的收藏/ }).click()
  await page.getByTestId('add-setup-step').click()
  await page.getByTestId('endpoint-picker-search').fill('查询我的收藏')
  await page.locator('.endpoint-picker-option').filter({ hasText: '查询我的收藏' }).click()
  await acceptExecutionConfirmation(page, () => page.getByTestId('setup-preview-0').click())
  await expect(page.getByText('已到达第 1 步')).toBeVisible()
  await page.getByTestId('workflow-preview-select-json_path:$.data[0].id').check()
  await page.getByTestId('workflow-preview-target-json_path:$.data[0].id').fill('favoriteId')
  await page.getByTestId('workflow-preview-apply').click()
  await expect(page.getByTestId('setup-0-extraction-target-0')).toHaveValue('favoriteId')
  await page.getByTestId('assertion-expected-0').fill('200')
  await acceptExecutionConfirmation(page, () => page.getByRole('button', { name: '保存并调试' }).click())
  await expect(page.locator('.result-status').getByText('通过', { exact: true })).toBeVisible()
  await page.getByTestId('adopt-baseline').click()
  await expect(page.getByTestId('baseline-success')).toBeVisible()
  await page.getByTitle('关闭调试').click()

  for (const summary of ['添加收藏', '取消收藏']) {
    await page.getByTestId('endpoint-search').fill(summary)
    await page.locator('.endpoint-tree').getByRole('button', { name: new RegExp(summary) }).click()
    await acceptExecutionConfirmation(page, () => page.getByRole('button', { name: '保存并调试' }).click())
    await expect(page.locator('.result-status').getByText('通过', { exact: true })).toBeVisible()
    await page.getByTestId('adopt-baseline').click()
    await expect(page.getByTestId('baseline-success')).toBeVisible()
    await page.getByTitle('关闭调试').click()
  }

  await page.getByRole('link', { name: '用例管理' }).click()
  await expect(page.getByTestId('cases-page')).toBeVisible()
  await page.getByTestId('case-work-view-baseline').click()
  await expect(page.locator('[data-testid^="case-version-baseline-"]').first()).toBeVisible()
  await page.locator('[data-testid^="case-version-baseline-"]').first().click()
  await expect(page.getByRole('heading', { name: '基线用例' })).toBeVisible()
  await page.getByRole('button', { name: '检查断言' }).click()
  await expect(page.getByTestId('baseline-audit-summary')).toBeVisible()
  await page.getByPlaceholder('搜索用例、接口或路径').fill('查询我的收藏')
  await expect(page.getByText('查询我的收藏', { exact: false }).first()).toBeVisible()
  await page.getByRole('link', { name: '工作台' }).click()
  await expect(page.locator('.design-workspace')).toBeVisible()

  acceptance.useRegressionResponses()
  await page.evaluate(() => { window.__apiAcceptancePageMarker = 'preserved' })
  await acceptExecutionConfirmation(page, () => page.getByTestId('run-task').click())
  await expect(page).toHaveURL(/#\/runs\?executionId=/)
  await expect(page.getByText('开始执行用例', { exact: true }).first()).toBeVisible()
  await expect(page.getByTestId('overview-passed')).toContainText('1')
  await expect(page.getByTestId('overview-failed')).toContainText('1')
  await expect(page.getByTestId('overview-broken')).toContainText('1')
  await expect(page.getByRole('button', { name: '实时轨迹' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '实时日志' })).toBeVisible()
  expect(await page.evaluate(() => window.__apiAcceptancePageMarker)).toBe('preserved')
  await expect(page.locator('body')).not.toContainText(secret)
  await page.getByRole('button', { name: /添加收藏.*断言失败/ }).click()
  await expect(page.getByRole('dialog', { name: '执行详情' })).toBeVisible()
  await expect(page.getByText('AI 失败分析', { exact: true })).toBeVisible()
  await expect(page.getByText('qwen3.7-plus', { exact: true })).toBeVisible()
  await page.getByTitle('关闭详情').click()
  await page.getByTestId('execution-tab-cases').click()
  await expect(page.getByText('业务拒绝收藏').first()).toBeVisible()
  await assertNoHorizontalOverflow(page)
  await page.screenshot({ path: testInfo.outputPath('execution-desktop.png'), fullPage: true })

  await page.getByRole('link', { name: '测试报告' }).click()
  await page.getByTestId('report-history-row').first().click()
  await expect(page.getByText('项目报告驾驶舱', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '查看完整诊断' })).toBeVisible()
  await page.getByTestId('report-open-diagnostic').click()
  await expect(page.getByRole('heading', { name: '诊断结论' })).toBeVisible()
  await expect(page.getByText('产品失败', { exact: true })).toBeVisible()
  await expect(page.getByText('环境异常', { exact: true })).toBeVisible()
  await expect(page.getByText('AI 诊断摘要', { exact: true })).toBeVisible()
  await expect(page.getByText('技术日志', { exact: true })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('report-desktop.png'), fullPage: true })

  await page.setViewportSize({ width: 390, height: 844 })
  await assertNoHorizontalOverflow(page)
  await page.screenshot({ path: testInfo.outputPath('report-mobile.png'), fullPage: true })

  const report = await acceptance.readLatestExecution(page)
  expect(JSON.stringify(report)).not.toContain(secret)
  expect(acceptance.output()).not.toContain(secret)

  await page.setViewportSize({ width: 1440, height: 900 })
  await page.getByRole('link', { name: '定时任务', exact: true }).click()
  await page.getByTestId('scheduled-name').fill('Codex 隔离验收定时任务')
  await page.getByTestId('scheduled-target-option').first().click()
  await page.getByTestId('scheduled-enabled-toggle').click()
  await page.getByTestId('scheduled-save').click()
  const scheduledRow = page.locator('.scheduled-row').filter({ hasText: 'Codex 隔离验收定时任务' })
  await expect(scheduledRow).toContainText('当前已停用')
  await scheduledRow.getByTitle('编辑').click()
  await expect(page.getByRole('heading', { name: '编辑定时任务' })).toBeVisible()
  await page.getByTestId('scheduled-name').fill('Codex 隔离验收定时任务-已编辑')
  await page.getByTestId('scheduled-save').click()
  const editedScheduledRow = page.locator('.scheduled-row').filter({ hasText: 'Codex 隔离验收定时任务-已编辑' })
  await expect(editedScheduledRow).toBeVisible()
  page.once('dialog', dialog => dialog.accept())
  await editedScheduledRow.getByTitle('删除').click()
  await expect(editedScheduledRow).toHaveCount(0)

  await page.getByRole('link', { name: '任务管理', exact: true }).click()
  await page.locator('.task-list-item').first().click()
  await expect(page.getByTestId('selected-task-title')).toBeVisible()
  page.once('dialog', dialog => dialog.accept())
  await page.getByTestId('task-detail-delete').click()
  await expect(page.getByTestId('selected-task-title')).toHaveCount(0)
})

async function acceptExecutionConfirmation(page, click) {
  const dialogPromise = page.waitForEvent('dialog')
  const clickPromise = click()
  const dialog = await dialogPromise
  expect(dialog.message()).toContain('生产环境')
  expect(dialog.message()).toContain('真实发送')
  await dialog.accept()
  await clickPromise
}

async function assertNoHorizontalOverflow(page) {
  const diagnostic = await page.evaluate(() => {
    const viewportWidth = document.documentElement.clientWidth
    const overflow = document.documentElement.scrollWidth - viewportWidth
    const offenders = Array.from(document.querySelectorAll('body *'))
      .map(element => {
        const rect = element.getBoundingClientRect()
        return {
          tag: element.tagName.toLowerCase(),
          className: String(element.className || '').slice(0, 140),
          testId: element.getAttribute('data-testid') || '',
          left: Math.round(rect.left), right: Math.round(rect.right), width: Math.round(rect.width),
        }
      })
      .filter(item => item.right > viewportWidth + 1 || item.left < -1 || item.width > viewportWidth + 1)
      .sort((left, right) => right.right - left.right)
      .slice(0, 12)
    return { overflow, viewportWidth, offenders }
  })
  expect(diagnostic.overflow, `horizontal overflow: ${JSON.stringify(diagnostic)}`).toBeLessThanOrEqual(1)
}

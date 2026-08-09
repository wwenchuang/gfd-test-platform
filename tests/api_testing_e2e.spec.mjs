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
  await page.getByRole('button', { name: '新建项目' }).click()
  await page.getByLabel('项目名称').fill('3D 我的收藏')
  await page.getByRole('button', { name: '创建', exact: true }).click()
  await expect(page.getByRole('combobox', { name: '项目', exact: true })).toHaveValue(/.+/)
  await page.locator('input[type="file"]').setInputFiles(acceptance.openApiPath)
  await page.getByRole('button', { name: '读取并比较' }).click()
  await page.getByRole('button', { name: '确认保存' }).click()

  await page.getByRole('link', { name: '环境配置' }).click()
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
  await page.getByRole('button', { name: '应用范围' }).click()
  await expect(page.getByText('范围已保存')).toBeVisible()
  for (const summary of ['查询我的收藏', '添加收藏', '取消收藏']) {
    await page.getByRole('button', { name: new RegExp(summary) }).locator('..').getByRole('checkbox').check()
  }
  await page.getByRole('button', { name: '生成测试用例' }).click()
  await expect(page.getByText('已完成', { exact: true })).toBeVisible()
  await expect(page.locator('.design-workspace')).toBeVisible()
  await assertNoHorizontalOverflow(page)
  await page.screenshot({ path: testInfo.outputPath('workbench-desktop.png'), fullPage: true })
  await page.getByTitle('收起 AI 助手').click()
  await expect(page.locator('.ai-assistant')).toHaveClass(/collapsed/)
  await page.getByTitle('展开 AI 助手').click()

  await page.setViewportSize({ width: 390, height: 844 })
  await assertNoHorizontalOverflow(page)
  await page.screenshot({ path: testInfo.outputPath('workbench-mobile.png'), fullPage: true })
  await page.setViewportSize({ width: 1440, height: 900 })

  await page.getByRole('button', { name: /查询我的收藏/ }).click()
  await page.getByTestId('assertion-expected-0').fill('200')
  await page.getByRole('button', { name: '保存草稿' }).click()
  await page.getByRole('button', { name: '调试当前草稿' }).click()
  await page.getByTestId('debug-send').click()
  await expect(page.getByText('PASSED', { exact: true })).toBeVisible()
  await page.getByTestId('adopt-baseline').click()
  await page.getByTitle('关闭调试').click()

  for (const summary of ['添加收藏', '取消收藏']) {
    await page.getByRole('button', { name: new RegExp(summary) }).click()
    await page.getByRole('button', { name: '调试当前草稿' }).click()
    await page.getByTestId('debug-send').click()
    await expect(page.getByText('PASSED', { exact: true })).toBeVisible()
    await page.getByTestId('adopt-baseline').click()
    await page.getByTitle('关闭调试').click()
  }

  acceptance.useRegressionResponses()
  await page.getByRole('link', { name: '执行记录' }).click()
  await page.evaluate(() => { window.__apiAcceptancePageMarker = 'preserved' })
  await page.getByTestId('run-baselines').click()
  await expect(page.getByText('开始执行用例', { exact: true }).first()).toBeVisible()
  await expect(page.getByTestId('passed-count')).toHaveText('1')
  await expect(page.getByTestId('failed-count')).toHaveText('1')
  await expect(page.getByTestId('broken-count')).toHaveText('1')
  expect(await page.evaluate(() => window.__apiAcceptancePageMarker)).toBe('preserved')
  await expect(page.locator('body')).not.toContainText(secret)
  await page.getByRole('button', { name: /添加收藏.*FAILED/ }).click()
  await expect(page.getByText('AI 失败分析', { exact: true })).toBeVisible()
  await expect(page.getByText('qwen3.7-plus', { exact: true })).toBeVisible()
  await assertNoHorizontalOverflow(page)
  await page.screenshot({ path: testInfo.outputPath('report-desktop.png') })

  await page.setViewportSize({ width: 390, height: 844 })
  await assertNoHorizontalOverflow(page)
  await page.screenshot({ path: testInfo.outputPath('report-mobile.png') })

  const report = await acceptance.readLatestExecution(page)
  expect(JSON.stringify(report)).not.toContain(secret)
  expect(acceptance.output()).not.toContain(secret)
})

async function assertNoHorizontalOverflow(page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
}

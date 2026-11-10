import { expect, test } from 'playwright/test'

test('匿名登录页保持已记录的布局', async ({ page, baseURL }, testInfo) => {
  // A demonstration or CI run must never overwrite the reviewable reference image.
  expect(testInfo.config.updateSnapshots).toBe('none')
  const unexpectedRequests = []
  await page.route('**/*', route => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin === baseURL && request.method() === 'GET' && !url.pathname.startsWith('/api/')) {
      return route.continue()
    }
    unexpectedRequests.push(request.method() + ' ' + url.pathname)
    return route.abort()
  })

  await page.goto('/task-manager.html', { waitUntil: 'load' })
  const form = page.locator('#login-form')
  await expect(form.getByRole('heading', { name: '功夫豆测试平台' })).toBeVisible()
  await expect(form.getByLabel('用户名')).toBeEmpty()
  await expect(form.getByLabel('密码')).toBeEmpty()
  await expect(form.getByRole('button', { name: '登 录' })).toBeVisible()
  await form.locator('.brand-mark-img').evaluate(img => img.decode())
  await page.evaluate(() => document.fonts.ready)

  if (process.env.W06_VISUAL_SPACING_CHANGE === '1') {
    await form.locator('.login-logo').evaluate(logo => {
      logo.style.marginBottom = (parseFloat(getComputedStyle(logo).marginBottom) + 12) + 'px'
    })
  }
  expect(unexpectedRequests).toEqual([])
  await page.screenshot({ path: testInfo.outputPath('observed.png'), animations: 'disabled', caret: 'hide' })
  await expect(page).toHaveScreenshot('anonymous-login.png')
})

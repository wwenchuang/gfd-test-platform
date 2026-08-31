const assert = require('node:assert/strict');

module.exports = async function checkGatewayAuthentication(parent, url) {
  const context = await parent.context().browser().newContext();
  const page = await context.newPage();
  const headersSeen = [];
  let rejectGateway = false;
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  try {
    await page.route('**/ai-gateway/**', route => {
      const authorization = route.request().headers().authorization;
      headersSeen.push(Boolean(authorization?.startsWith('Bearer ')));
      if (!authorization || rejectGateway) return route.fulfill({status: 401, json: {ok: false, error: 'AI 网关鉴权失败'}});
      return route.continue();
    });
    await page.goto(url);
    assert.equal(await page.locator('#username').getAttribute('placeholder'), '请输入用户名');
    assert.equal(await page.locator('#password').getAttribute('placeholder'), '请输入密码');
    await page.locator('#login-form button[type="submit"]').click();
    assert.equal(await page.locator('#username').evaluate(el => el.validity.valueMissing), true);
    // Reproduce returning to the remembered model configuration after login.
    await page.evaluate(() => sessionStorage.setItem('midscene_active_workflow', 'config'));
    await page.fill('#username', 'admin');
    await page.fill('#password', 'visual-smoke-password');
    await page.locator('#login-form button[type="submit"]').click();
    await page.locator('#app').waitFor({state: 'visible'});
    await page.waitForTimeout(150);
    assert.ok(headersSeen.length >= 2, 'Remembered model page must request its provider and routing data');
    assert.ok(headersSeen.every(Boolean), 'Every AI gateway request must carry the main session');
    await page.locator('.workflow-step[data-workflow="config"]').click();
    await page.locator('.model-config-guide').waitFor();
    assert.equal(await page.locator('#app').isVisible(), true);
    rejectGateway = true;
    await page.getByRole('button', {name: '测试当前策略', exact: true}).click();
    await page.getByText(/AI 网关未接受当前登录凭证/).first().waitFor();
    assert.equal(await page.locator('#app').isVisible(), true, 'An independent gateway rejection must not erase a valid main session');
    assert.equal(await page.evaluate(() => Boolean(sessionStorage.getItem('sessionToken'))), true);
    await page.locator('#modal-model-test-result').getByRole('button', {name: '知道了', exact: true}).click();
    await page.route('**/api/auth/me', route => route.fulfill({status: 401, json: {ok: false, error: '会话已撤销'}}));
    await page.getByRole('button', {name: '测试当前策略', exact: true}).click();
    await page.locator('#login-screen').waitFor({state: 'visible'});
    assert.equal(await page.evaluate(() => Boolean(sessionStorage.getItem('sessionToken'))), false, 'Authoritative session revocation must still sign out');
  } catch (error) {
    error.message += `\nGateway regression state: ${JSON.stringify({headersSeen, pageErrors, text: (await page.locator('body').innerText()).slice(0,1200)})}`;
    throw error;
  } finally {
    await context.close();
  }
};

import { defineConfig } from 'playwright/test'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../../', import.meta.url))
const baseURL = 'http://127.0.0.1:17665'

export default defineConfig({
  testDir: '.',
  testMatch: 'login.spec.mjs',
  workers: 1,
  retries: 0,
  reporter: 'list',
  outputDir: '../../test-results/week6-visual',
  snapshotPathTemplate: '{testDir}/baselines/{arg}{ext}',
  updateSnapshots: 'none',
  expect: {
    toHaveScreenshot: { animations: 'disabled', caret: 'hide', threshold: 0, maxDiffPixels: 0 },
  },
  webServer: {
    command: 'python3 -m http.server 17665 --bind 127.0.0.1',
    cwd: root,
    url: baseURL + '/task-manager.html',
    reuseExistingServer: false,
    stderr: 'ignore',
  },
  use: {
    baseURL,
    browserName: 'chromium',
    viewport: { width: 960, height: 720 },
    deviceScaleFactor: 1,
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    colorScheme: 'dark',
    reducedMotion: 'reduce',
    serviceWorkers: 'block',
  },
})

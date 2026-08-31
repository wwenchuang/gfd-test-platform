import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import { router } from './router'
import { requireApiTestingSession, verifyApiTestingSession } from './utils/authRedirect'
import { loadTestApplications } from './utils/testApplications'
import './styles/tokens.css'
import './styles/app.css'

async function mountVerifiedApp(): Promise<void> {
  if (!await verifyApiTestingSession()) return
  await Promise.race([
    loadTestApplications(),
    new Promise<void>(resolve => window.setTimeout(resolve, 800)),
  ])
  createApp(App).use(createPinia()).use(router).mount('#app')
}

async function bootstrap(): Promise<void> {
  await mountVerifiedApp().catch(error => {
    const host = document.getElementById('app')
    if (!host) return
    host.replaceChildren()
    const message = document.createElement('p')
    message.setAttribute('role', 'alert')
    message.textContent = error instanceof Error && error.name !== 'AbortError' ? error.message : '会话验证超时，请重试。'
    const retry = document.createElement('button')
    retry.textContent = '重新验证会话'
    retry.onclick = () => { retry.disabled = true; void bootstrap() }
    host.append(message, retry)
  })
}
if (requireApiTestingSession()) void bootstrap()

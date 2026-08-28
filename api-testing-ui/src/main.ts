import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import { router } from './router'
import { requireApiTestingSession } from './utils/authRedirect'
import { loadTestApplications } from './utils/testApplications'
import './styles/tokens.css'
import './styles/app.css'

async function bootstrap(): Promise<void> {
  await Promise.race([
    loadTestApplications(),
    new Promise<void>(resolve => window.setTimeout(resolve, 800)),
  ])
  createApp(App).use(createPinia()).use(router).mount('#app')
}

if (requireApiTestingSession()) void bootstrap()

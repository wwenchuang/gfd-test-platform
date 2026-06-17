import fs from 'fs/promises';
import path from 'path';
import {fileURLToPath} from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const LOG_FILE = path.join(__dirname, '..', 'logs', 'agent-runs.jsonl');

function preview(value, limit = 500) {
  const text = typeof value === 'string' ? value : JSON.stringify(value ?? '');
  return text.replace(/\s+/g, ' ').slice(0, limit);
}

function redact(text) {
  return String(text || '')
    .replace(/sk-[A-Za-z0-9_-]+/g, 'sk-***')
    .replace(/figd_[A-Za-z0-9_-]+/g, 'figd_***');
}

export async function appendAgentLog(entry) {
  const payload = {
    time: new Date().toISOString(),
    runId: entry.runId,
    traceId: entry.traceId,
    state: entry.state,
    tool: entry.tool || '',
    success: Boolean(entry.success),
    durationMs: Number(entry.durationMs || 0),
    inputPreview: redact(preview(entry.input)),
    outputPreview: redact(preview(entry.output)),
    error: redact(entry.error || ''),
  };
  await fs.mkdir(path.dirname(LOG_FILE), {recursive: true});
  await fs.appendFile(LOG_FILE, JSON.stringify(payload) + '\n', 'utf8');
}

export {preview};

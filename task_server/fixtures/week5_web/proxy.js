'use strict';
const http = require('node:http');
const {allowRequest, responseContainsSecret} = require('./policy');

// port is injected only by the local unit test; production sets the fixed
// configured loopback port, never a value from the request or browser.
function readLoopback(path, secret, port = 8088, catalogQueries = new Set()) {
  const kind = typeof path === 'string' && allowRequest('http://127.0.0.1:8088', 'GET', `http://127.0.0.1:8088${path}`, catalogQueries);
  if (!kind || !Number.isInteger(port) || port < 1 || port > 65535) return Promise.reject(new Error('blocked'));
  const headers = {'Accept-Encoding': 'identity', 'Cache-Control':'no-store'};
  if (kind === 'api') headers.Authorization = `Bearer ${secret}`;
  return new Promise((resolve, reject) => {
    let done = false;
    const fail = () => { if (!done) { done = true; reject(new Error('read_blocked')); } };
    const req = http.request({hostname:'127.0.0.1',port,path,method:'GET',headers,agent:false}, res => {
      if (res.statusCode >= 300 && res.statusCode < 400 || (res.headers['content-encoding'] && res.headers['content-encoding'] !== 'identity')) {
        res.resume(); fail(); return;
      }
      const chunks = []; let size = 0;
      res.on('data', chunk => { size += chunk.length; if (size > 10 * 1024 * 1024) { req.destroy(); fail(); } else chunks.push(chunk); });
      res.on('aborted', fail); res.on('error', fail);
      res.on('end', () => {
        if (done) return;
        const body = Buffer.concat(chunks);
        if (responseContainsSecret(body, secret)) { fail(); return; }
        done = true;
        const contentType = String(res.headers['content-type'] || '').split(';')[0].toLowerCase();
        const allowed = new Set(['application/json','text/html','text/javascript','application/javascript','text/css','image/png','application/octet-stream']);
        resolve({status:res.statusCode, body, contentType:allowed.has(contentType) ? contentType : 'application/octet-stream'});
      });
    });
    req.setTimeout(5000, () => { req.destroy(); fail(); });
    req.on('error', fail); req.end();
  });
}

module.exports = {readLoopback};

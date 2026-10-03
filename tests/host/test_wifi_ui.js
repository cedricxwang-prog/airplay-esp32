/* Run the actual WebUI script with a small DOM and HTTP fixture. No device IO. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../../data/www/index.html'), 'utf8');
const source = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
new vm.Script(source);

class Element {
  constructor() {
    this.children = []; this.disabled = false; this.hidden = false;
    this.value = ''; this.checked = false; this.style = {}; this.dataset = {};
    this.className = ''; this.textContent = '';
    this.classList = {
      add: name => { if (!this.className.split(' ').includes(name)) this.className += ' ' + name; },
      remove: name => { this.className = this.className.split(' ').filter(c => c !== name).join(' '); }
    };
  }
  replaceChildren(...children) { this.children = children; }
  appendChild(child) { this.children.push(child); }
  append(...children) { this.children.push(...children); }
  focus() { this.focused = true; }
  scrollIntoView() { this.scrolledIntoView = true; }
}

function reply(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function fixture(options = {}) {
  const elements = new Map();
  const state = { now: 1000000, timers: new Map(), nextTimer: 0, posts: [], polls: 0, accepted: false };
  const base = {
    ip: '192.0.2.50', mac: '02:00:00:00:00:50', device_name: 'Test Receiver',
    firmware_version: 'test', free_heap: 1048576, uptime_s: 200,
    wifi_connected: true, wifi_ssid: 'Example Old', wifi_bssid: '02:00:00:00:00:01'
  };
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const context = {
    document: { getElementById: get, createElement: () => new Element(), querySelectorAll: () => [], documentElement: {} },
    localStorage: { getItem: () => options.language || 'en', setItem: () => {} },
    window: {}, console, AbortController, TextEncoder,
    Date: { now: () => state.now },
    setTimeout: (callback, delay) => {
      const id = ++state.nextTimer; state.timers.set(id, { callback, delay });
      if (delay === 3000) queueMicrotask(() => {
        if (state.timers.delete(id)) { state.now += delay; callback(); }
      });
      return id;
    },
    clearTimeout: id => state.timers.delete(id), setInterval: () => 0,
    fetch: async (url, request = {}) => {
      if (url === '/api/wifi/scan') {
        if (options.scan) return options.scan(request, state);
        return reply({ success: true, networks: options.networks || [] });
      }
      if (url === '/api/wifi/saved') return reply({ success: true, networks: options.saved || [] });
      if (url === '/api/system/info') {
        let info = { ...base, ...(options.base || {}), uptime_s: 200 + (state.now - 1000000) / 1000 };
        if (state.accepted && options.poll) info = options.poll(++state.polls, info, state);
        if (info === null) throw new Error('Device is restarting');
        return reply({ success: true, info });
      }
      if (url === '/api/wifi/config') {
        assert.equal(request.method, 'POST');
        const payload = JSON.parse(request.body); state.posts.push(payload);
        if (options.post) return options.post(payload, state, request);
        state.accepted = true; return reply({ success: true, restart_required: true });
      }
      throw new Error('Unexpected fixture URL: ' + url);
    }
  };
  vm.createContext(context); vm.runInContext(source, context);
  return { context, state, get };
}

const secure = { ssid: 'Example Secure', secure: true, connected: false, bssid: '02:00:00:00:00:02', rssi: -50 };

(async () => {
  // Actual scan row click opens the password panel, preserves the chosen SSID,
  // focuses the password, and distinguishes BSSIDs sharing one SSID.
  let f = fixture({ networks: [secure, { ...secure, bssid: '02:00:00:00:00:03' }], base: { wifi_ssid: secure.ssid, wifi_bssid: secure.bssid } });
  await f.context.loadInfo(); await f.context.scanWiFi();
  const rows = f.get('wifi-list').children;
  assert.match(rows[0].children[0].children[1].textContent, /Connected/);
  assert.doesNotMatch(rows[1].children[0].children[1].textContent, /Connected/);
  rows[0].onclick();
  assert.equal(f.get('wifi-connect-panel').hidden, false);
  assert.equal(f.get('wifi-selected-name').textContent, secure.ssid);
  assert.equal(f.get('wifi-ssid').value, secure.ssid);
  assert.equal(f.get('wifi-selected-password').focused, true);
  assert.equal(f.get('wifi-selected-password').disabled, false);
  assert.equal(f.get('wifi-reuse-row').hidden, true);

  // Empty/short secure passwords and oversized UTF-8 SSIDs do not issue POSTs.
  await f.context.connectSelectedWiFi();
  f.get('wifi-selected-password').value = 'short'; await f.context.connectSelectedWiFi();
  await f.context.submitWiFiConnection({ ssid: '界'.repeat(11), password: 'example-pass' });
  assert.equal(f.state.posts.length, 0);
  assert.equal(f.context.validWiFiSSID('界'.repeat(10)), true);
  assert.equal(f.context.validWiFiPassword('界'.repeat(21)), true);
  assert.equal(f.context.validWiFiPassword('界'.repeat(22)), false);
  assert.equal(f.context.validWiFiPassword('a'.repeat(64)), true);
  assert.equal(f.context.validWiFiPassword('z'.repeat(64)), false);
  assert.equal(f.context.validWiFiSSID('nul\0ssid'), false);

  // Save acknowledgment only shows pending. A stale same-SSID response before
  // reboot cannot complete the request; reboot + actual association can.
  const pendingMessages = [];
  f = fixture({ networks: [secure], base: { wifi_ssid: secure.ssid }, poll: (count, info) => {
    pendingMessages.push(f.get('wifi-msg').children[0].textContent);
    return count === 1 ? info : { ...info, uptime_s: 3, wifi_ssid: secure.ssid, wifi_bssid: secure.bssid };
  } });
  await f.context.scanWiFi(); f.get('wifi-list').children[0].onclick();
  f.get('wifi-selected-password').value = 'example-pass'; await f.context.connectSelectedWiFi();
  assert.deepEqual(f.state.posts, [{ ssid: secure.ssid, password: 'example-pass' }]);
  assert.equal(f.state.polls, 2);
  assert.match(pendingMessages[0], /^Saved\. Restarting/);
  assert.match(f.get('wifi-msg').children[0].textContent, /^Connected: Example Secure$/);
  assert.equal(f.get('wifi-selected-password').value, '');
  assert.equal(f.get('wifi-selected-connect').disabled, false);
  assert.equal(f.state.timers.size, 0);

  // A saved row defaults to device-side password reuse; its checkbox permits
  // a new password. No saved-password readback endpoint is called.
  f = fixture({ networks: [secure], saved: [secure.ssid], poll: (count, info) => ({ ...info, uptime_s: 2, wifi_ssid: secure.ssid, wifi_bssid: secure.bssid }) });
  await f.context.loadSavedWiFi(); await f.context.scanWiFi(); f.get('wifi-list').children[0].onclick();
  assert.match(f.get('wifi-list').children[0].children[0].children[1].textContent, /Saved/);
  assert.equal(f.get('wifi-reuse-row').hidden, false); assert.equal(f.get('wifi-reuse').checked, true);
  assert.equal(f.get('wifi-selected-password').disabled, true);
  f.get('wifi-reuse').checked = false; f.context.updateWiFiPasswordPanel();
  assert.equal(f.get('wifi-selected-password').disabled, false);
  f.get('wifi-reuse').checked = true; f.context.updateWiFiPasswordPanel();
  await f.context.connectSelectedWiFi(); assert.deepEqual(f.state.posts, [{ ssid: secure.ssid, use_saved: true }]);

  // An open row sends an empty password; legal SSID spaces are preserved.
  const open = { ...secure, ssid: ' Example Open ', secure: false };
  f = fixture({ networks: [open], poll: (count, info) => ({ ...info, uptime_s: 2, wifi_ssid: open.ssid, wifi_bssid: open.bssid }) });
  await f.context.scanWiFi(); f.get('wifi-list').children[0].onclick();
  assert.equal(f.get('wifi-selected-password-group').hidden, true);
  await f.context.connectSelectedWiFi(); assert.deepEqual(f.state.posts, [{ ssid: open.ssid, password: '' }]);

  // A rejected save and a reboot that reconnects to a different network must
  // never display the selected network as connected.
  f = fixture({ post: () => reply({ success: false, error: 'ESP_ERR_NOT_FOUND' }, 404) });
  await f.context.submitWiFiConnection({ ssid: secure.ssid, use_saved: true });
  assert.match(f.get('wifi-msg').children[0].textContent, /Connection unconfirmed.*ESP_ERR_NOT_FOUND/);
  assert.equal(f.state.polls, 0); assert.equal(f.get('wifi-scan').disabled, false);
  f = fixture({ poll: (count, info) => ({ ...info, uptime_s: count, wifi_ssid: 'Example Different' }) });
  await f.context.submitWiFiConnection({ ssid: secure.ssid, password: 'example-pass' });
  assert.equal(f.state.polls, 15);
  assert.match(f.get('wifi-msg').children[0].textContent, /^Saved, but connection is unconfirmed/);
  assert.doesNotMatch(f.get('wifi-msg').children[0].textContent, /^Connected:/);

  // Text-only rendering of an untrusted SSID/error and accurate empty/busy UI.
  const untrusted = '<img src=x onerror=alert(1)>';
  f = fixture({ networks: [{ ...secure, ssid: untrusted }] });
  await f.context.scanWiFi(); f.get('wifi-list').children[0].onclick();
  assert.equal(f.get('wifi-selected-name').textContent, untrusted);
  assert.equal(f.get('wifi-selected-name').children.length, 0);
  f = fixture({ scan: () => reply({ success: false, error: untrusted }, 500) });
  await f.context.scanWiFi(); assert.equal(f.get('wifi-list').children[0].textContent, 'Scan failed: ' + untrusted);
  f = fixture(); await f.context.scanWiFi(); assert.equal(f.get('wifi-list').children[0].textContent, 'No networks found');
  f = fixture({ language: 'zh', scan: () => reply({ success: false, error: 'ESP_ERR_WIFI_STATE' }, 503) });
  await f.context.scanWiFi(); assert.match(f.get('wifi-list').children[0].textContent, /WiFi 正忙.*ESP_ERR_WIFI_STATE/);

  // Timeout and duplicate clicks share one request and always restore controls.
  let calls = 0;
  f = fixture({ scan: request => { calls++; return new Promise((resolve, reject) => request.signal.addEventListener('abort', () => { const e = new Error('aborted'); e.name = 'AbortError'; reject(e); })); } });
  const scan = f.context.scanWiFi(); await f.context.scanWiFi(); assert.equal(calls, 1);
  [...f.state.timers.values()].find(t => t.delay === 20000).callback(); await scan;
  assert.match(f.get('wifi-list').children[0].textContent, /Request timed out/);
  assert.equal(f.get('wifi-scan').disabled, false); assert.equal(f.state.timers.size, 0);

  f = fixture({ post: (payload, state, request) => new Promise((resolve, reject) => {
    const timer = [...state.timers.values()].find(t => t.delay === 15000);
    request.signal.addEventListener('abort', () => { const e = new Error('aborted'); e.name = 'AbortError'; reject(e); });
    queueMicrotask(() => timer.callback());
  }) });
  const connect = f.context.submitWiFiConnection({ ssid: secure.ssid, password: 'example-pass' });
  await f.context.submitWiFiConnection({ ssid: secure.ssid, password: 'example-pass' }); await connect;
  assert.equal(f.state.posts.length, 1); assert.match(f.get('wifi-msg').children[0].textContent, /Request timed out/);
  assert.equal(f.get('wifi-selected-connect').disabled, false); assert.equal(f.state.timers.size, 0);

  console.log('WiFi WebUI tests passed: actual scan selection/password focus, BSSID status, UTF-8 validation, saved reuse, open networks, save/reboot confirmation, failed connection, timeout/duplicate guard, and safe text.');
})().catch(error => { console.error(error); process.exitCode = 1; });

/* Exercise the actual WebUI JavaScript. Fixtures never access a device. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../../data/www/index.html'), 'utf8');
const source = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
new vm.Script(source);

class Element {
  constructor() {
    this.children = []; this.files = []; this.disabled = false; this.hidden = false;
    this.value = ''; this.style = {}; this.dataset = {}; this.className = ''; this.textContent = '';
    this.classList = { add() {}, remove() {} };
  }
  replaceChildren(...children) { this.children = children; }
  appendChild(child) { this.children.push(child); }
  append(...children) { this.children.push(...children); }
}

function image({ version = '0.2.7', chip = 9, lengths = [256, 16], hash = true } = {}) {
  let end = 24;
  for (const length of lengths) end += 8 + length;
  const bytes = Buffer.alloc((Math.floor(end / 16) + 1) * 16 + (hash ? 32 : 0));
  bytes[0] = 0xe9; bytes[1] = lengths.length; bytes[23] = hash ? 1 : 0;
  bytes.writeUInt16LE(chip, 12);
  let offset = 24;
  for (const length of lengths) {
    bytes.writeUInt32LE(length, offset + 4); offset += 8 + length;
  }
  bytes.writeUInt32LE(0xabcd5432, 32);
  Buffer.from(version).copy(bytes, 48, 0, 31);
  return bytes;
}

function file(bytes = image(), options = {}) {
  return {
    name: options.name || 'application.bin', size: bytes.length,
    slice(start, end) {
      return { arrayBuffer: async () => {
        if (options.read) await options.read(start, end);
        return Uint8Array.from(bytes.subarray(start, end)).buffer;
      } };
    }
  };
}

function response(body = '', status = 200, headers = {}) {
  return {
    ok: status >= 200 && status < 300, status, headers: new Headers(headers),
    text: async () => typeof body === 'string' ? body : JSON.stringify(body),
    json: async () => body
  };
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}

function untilAbort(signal, state) {
  return new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => { state.aborts++; reject(new Error('Aborted')); }, { once: true });
  });
}

function fixture(options = {}) {
  const elements = new Map();
  const state = {
    now: 1000000, nextTimer: 0, timers: new Map(), posts: [], polls: 0,
    pages: 0, infoRequests: 0, aborts: 0, reloads: [], history: [], attempted: false
  };
  const before = {
    firmware_version: '0.2.6', boot_id: 'old-boot', uptime_s: 200,
    image_chip_id: 9, ota_partition_size: 3 * 1024 * 1024,
    mac: '02:00:00:00:00:50', ...(options.before || {})
  };
  const after = {
    ...before, firmware_version: '0.2.7', boot_id: 'new-boot', uptime_s: 3,
    webui_version: '0.2.7', webui_source: 'embedded', ...(options.after || {})
  };
  const get = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  get('fw-file').files = options.noFile ? [] : [options.file || file()];
  const context = {
    document: { getElementById: get, createElement: () => new Element(), querySelectorAll: () => [], documentElement: {} },
    localStorage: { getItem: () => options.language || 'en', setItem() {} },
    window: { location: { replace: url => state.reloads.push(url) } },
    console, AbortController, TextEncoder,
    Date: { now: () => state.now },
    setTimeout(callback, delay) {
      const id = ++state.nextTimer;
      state.timers.set(id, { callback, at: state.now + delay }); return id;
    },
    clearTimeout: id => state.timers.delete(id), setInterval: () => 0,
    fetch: async (url, request = {}) => {
      assert.equal(request.cache, url === '/api/ota/update' ? undefined : 'no-store');
      if (url === '/api/system/info') {
        state.infoRequests++;
        if (!state.attempted) {
          if (options.baselineFailure) throw new Error('Offline');
          return response({ success: true, info: before });
        }
        state.polls++;
        state.history.push(get('ota-msg').children[0]?.textContent || '');
        const info = options.poll ? await options.poll(state.polls, after, state, request) : { ...after, uptime_s: 3 + state.polls };
        if (info === null) throw new Error('Restarting');
        return response({ success: true, info });
      }
      if (url === '/api/ota/update') {
        assert.equal(request.method, 'POST');
        assert.equal(request.headers['Content-Type'], 'application/octet-stream');
        state.attempted = true; state.posts.push(request);
        return options.post ? options.post(request, state) : response('Firmware update complete, rebooting now!\n');
      }
      if (url === '/') {
        state.pages++;
        return options.page ? options.page(request, state) : response('<!DOCTYPE html><html>Bundled UI</html>', 200, {
          'X-WebUI-Version': '0.2.7', 'X-WebUI-Source': 'embedded'
        });
      }
      throw new Error('Unexpected fixture URL: ' + url);
    }
  };
  vm.createContext(context); vm.runInContext(source, context);
  return {
    context, state, get, before, after,
    advance(delay) {
      const target = state.now + delay;
      while (true) {
        const next = [...state.timers].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        state.now = next[1].at; state.timers.delete(next[0]); next[1].callback();
      }
      state.now = target;
    }
  };
}

async function flush() { for (let i = 0; i < 100; i++) await Promise.resolve(); }

async function drive(f, promise) {
  let done = false, value, error;
  promise.then(v => { done = true; value = v; }, e => { done = true; error = e; });
  for (let step = 0; step < 1000 && !done; step++) {
    await flush(); if (done) break;
    const next = [...f.state.timers.values()].sort((a, b) => a.at - b.at)[0];
    assert.ok(next, 'An unresolved operation must have a bounded timer');
    f.advance(next.at - f.state.now);
  }
  assert.ok(done, 'Fixture operation exceeded bounded execution');
  if (error) throw error; return value;
}

function message(f) { return f.get('ota-msg').children[0]?.textContent || ''; }
function unlocked(f) {
  assert.equal(f.context.otaBusy, false);
  assert.equal(f.get('fw-file').disabled, false);
  assert.equal(f.get('fw-choose').disabled, false);
  assert.equal(f.state.timers.size, 0);
}

(async () => {
  let checks = 0, realImageChecks = 0;
  // Parse local real artifacts when available, while keeping a clean checkout
  // runnable without downloading a release or building firmware first.
  let f = fixture();
  const releasePath = path.join(__dirname, '../../releases/0.2.6/AirPlay-ESP32S3-0.2.6-ota.bin');
  if (fs.existsSync(releasePath)) {
    const release = fs.readFileSync(releasePath);
    const released = await f.context.validateOTAFirmware(file(release), f.before);
    assert.equal(released.version, '0.2.6'); assert.equal(released.chipId, 9); assert.equal(released.size, release.length);
    checks++; realImageChecks++;
  }

  // Structural and target-metadata rejection must happen before POST.
  const badImages = [
    bytes => { bytes[0] = 0; },
    bytes => { bytes[1] = 0; },
    bytes => { bytes[1] = 17; },
    bytes => { bytes[23] = 2; },
    bytes => { bytes.writeUInt16LE(0xffff, 12); },
    bytes => { bytes.writeUInt16LE(5, 12); },
    bytes => { bytes.writeUInt32LE(0, 32); }, // bootloader, no app descriptor
    bytes => { bytes.writeUInt32LE(255, 28); },
    bytes => { bytes.fill(0, 48, 80); },
    bytes => { bytes.fill(65, 48, 80); }, // unterminated version
    bytes => { bytes[48] = 1; },
    bytes => { bytes.writeUInt32LE(bytes.length, 292); } // segment runs past EOF
  ];
  for (const mutate of badImages) {
    const bytes = image(); mutate(bytes); f = fixture({ file: file(bytes) });
    await drive(f, f.context.startOTA()); assert.equal(f.state.posts.length, 0);
    assert.match(message(f), /^Not uploaded:/); unlocked(f); checks++;
  }
  for (const bytes of [Buffer.alloc(20), image().subarray(0, image().length - 1), Buffer.concat([image(), image()])]) {
    f = fixture({ file: file(bytes, { name: 'apparently-valid.bin' }) });
    await drive(f, f.context.startOTA()); assert.equal(f.state.posts.length, 0); unlocked(f); checks++;
  }
  f = fixture({ file: { name: 'huge.bin', size: 128 * 1024 * 1024 + 1 } });
  await drive(f, f.context.startOTA()); assert.equal(f.state.posts.length, 0); unlocked(f); checks++;
  f = fixture({ before: { ota_partition_size: image().length - 1 } });
  await drive(f, f.context.startOTA()); assert.match(message(f), /exceeds/); assert.equal(f.state.posts.length, 0); unlocked(f); checks++;
  assert.equal((await f.context.validateOTAFirmware(file(), { ota_partition_size: image().length, image_chip_id: 9 })).version, '0.2.7'); checks++;
  // Missing/invalid old metadata is not converted to a false model/capacity match.
  assert.equal((await f.context.validateOTAFirmware(file(image({ chip: 20 })), { chip_model: 'ESP32', ota_partition_size: '1', image_chip_id: '9' })).chipId, 20); checks++;
  assert.equal((await f.context.validateOTAFirmware(file(image({ hash: false })), {})).version, '0.2.7'); checks++;

  f = fixture({ noFile: true }); await drive(f, f.context.startOTA());
  assert.equal(f.state.infoRequests, 0); assert.equal(f.state.posts.length, 0); assert.match(message(f), /Select/); checks++;
  f = fixture({ baselineFailure: true }); await drive(f, f.context.startOTA());
  assert.equal(f.state.posts.length, 0); assert.match(message(f), /nothing uploaded/); unlocked(f); checks++;
  f = fixture({ before: { image_chip_id: 5 } }); await drive(f, f.context.startOTA());
  f.context.uiLanguage = 'zh'; f.context.renderOTAStatus();
  assert.match(message(f), /固件芯片与设备不匹配/); unlocked(f); checks++;
  f = fixture({ file: file(image(), { read: () => new Promise(() => {}) }) });
  await drive(f, f.context.startOTA()); assert.equal(f.state.posts.length, 0);
  assert.match(message(f), /Firmware read timed out/); unlocked(f); checks++;

  // Raw File identity, successful text response, two new-boot observations,
  // then actual served-page headers and a cache-busting reload.
  const selected = file(); f = fixture({ file: selected });
  await drive(f, Promise.all([f.context.startOTA(), f.context.startOTA()]));
  assert.equal(f.state.posts.length, 1); assert.equal(f.state.posts[0].body, selected);
  assert.equal(f.state.polls, 2); assert.equal(f.state.pages, 1);
  assert.match(message(f), /^Verified:/); assert.match(f.state.history[0], /^Upload accepted;/);
  assert.equal(f.context.otaBusy, true); assert.equal(f.get('fw-file').disabled, true);
  assert.equal(f.state.timers.size, 1); f.advance(2000);
  assert.match(f.state.reloads[0], /^\/\?ui=0\.2\.7&t=\d+$/); assert.equal(f.state.timers.size, 0); checks++;

  // The lock also covers a delayed image read and delayed POST, not only polling.
  const readGate = deferred(); let firstRead = true;
  f = fixture({ file: file(image(), { read: async () => { if (firstRead) { firstRead = false; await readGate.promise; } } }) });
  let running = f.context.startOTA(); await flush();
  await f.context.startOTA(); assert.equal(f.state.posts.length, 0); assert.equal(f.get('fw-choose').disabled, true);
  readGate.resolve(); await drive(f, running); assert.equal(f.state.posts.length, 1); checks++;
  const postGate = deferred(); f = fixture({ post: () => postGate.promise });
  running = f.context.startOTA(); await flush();
  await f.context.startOTA(); assert.equal(f.state.posts.length, 1);
  postGate.resolve(response('OK')); await drive(f, running); assert.equal(f.state.posts.length, 1); checks++;

  // A network exception is uncertain, not failure or success. Only the boot,
  // target versions, identity and actual page can establish completion.
  f = fixture({ post: () => { throw new Error('Connection reset'); }, poll: (n, info) => n === 1 ? null : { ...info, uptime_s: n } });
  await drive(f, f.context.startOTA()); assert.equal(f.state.polls, 3); assert.match(message(f), /^Verified:/);
  assert.match(f.state.history[0], /^Upload response interrupted;/); checks++;
  f = fixture({ post: () => ({ ...response('OK'), text: async () => { throw new Error('Response truncated'); } }) });
  await drive(f, f.context.startOTA()); assert.match(message(f), /^Verified:/); checks++;

  // Clear HTTP failures are never repaired into a false successful result.
  const hostile = '<img src=x onerror=alert(1)>';
  f = fixture({ post: () => response(hostile, 500) }); await drive(f, f.context.startOTA());
  assert.equal(f.state.polls, 0); assert.equal(f.state.pages, 0);
  assert.equal(message(f), 'Upload failed: HTTP 500 — ' + hostile);
  assert.equal(f.get('ota-msg').children[0].children.length, 0); unlocked(f);
  f.advance(10000); assert.match(message(f), /^Upload failed:/); // persistent, not msg()'s 4s timer
  f.context.uiLanguage = 'zh'; f.context.renderOTAStatus(); assert.match(message(f), /^上传失败：HTTP 500/); checks++;
  f = fixture({ post: () => ({ ...response('', 413), text: async () => { throw new Error('Closed'); } }) });
  await drive(f, f.context.startOTA()); assert.equal(f.state.polls, 0); assert.match(message(f), /HTTP 413/); unlocked(f); checks++;

  // 200 acceptance cannot confirm any individual mismatch, missing field,
  // stale boot or response from another device.
  const mismatches = [
    { firmware_version: '0.2.70' }, { webui_version: '0.2.6' },
    { webui_source: 'spiffs' }, { webui_version: undefined },
    { boot_id: 'old-boot' }, { boot_id: undefined },
    { mac: '02:00:00:00:00:51' }
  ];
  for (const after of mismatches) {
    f = fixture({ after }); await drive(f, f.context.startOTA());
    assert.match(message(f), /^Update unconfirmed:/); assert.equal(f.state.reloads.length, 0);
    assert.ok(f.state.now <= 1090000); unlocked(f); checks++;
  }
  for (const headers of [
    { 'X-WebUI-Version': '0.2.6', 'X-WebUI-Source': 'embedded' },
    { 'X-WebUI-Version': '0.2.7', 'X-WebUI-Source': 'spiffs' }, {}
  ]) {
    f = fixture({ page: () => response('old page', 200, headers) });
    await drive(f, f.context.startOTA()); assert.match(message(f), /Served WebUI version or source/);
    assert.equal(f.state.reloads.length, 0); assert.ok(f.state.pages > 0); unlocked(f); checks++;
  }
  f = fixture({ page: (request, state) => {
    state.now += 90000;
    return response('Bundled UI', 200, { 'X-WebUI-Version': '0.2.7', 'X-WebUI-Source': 'embedded' });
  } });
  await drive(f, f.context.startOTA()); assert.match(message(f), /^Update unconfirmed:/);
  assert.equal(f.state.reloads.length, 0); unlocked(f); checks++;

  // Repeated same-version upload needs actual restart proof, especially near
  // boot time. Zero uptime and an outage by themselves cannot prove a reset.
  f = fixture({ before: { firmware_version: '0.2.7', uptime_s: 0 }, after: { boot_id: 'old-boot' }, poll: (n, info) => ({ ...info, uptime_s: 0 }) });
  await drive(f, f.context.startOTA()); assert.match(message(f), /restart has not been confirmed/); unlocked(f); checks++;
  f = fixture({ before: { firmware_version: '0.2.7', uptime_s: 0 }, poll: (n, info) => ({ ...info, uptime_s: 0 }) });
  await drive(f, f.context.startOTA()); assert.match(message(f), /^Verified:/); checks++;
  f = fixture({ before: { firmware_version: '0.2.7', boot_id: undefined, uptime_s: 0 }, after: { boot_id: undefined }, poll: (n, info) => n === 1 ? null : { ...info, uptime_s: 0 } });
  await drive(f, f.context.startOTA()); assert.match(message(f), /^Update unconfirmed:/); unlocked(f); checks++;
  f = fixture({ before: { firmware_version: '0.2.7', boot_id: undefined }, after: { boot_id: undefined } });
  await drive(f, f.context.startOTA()); assert.match(message(f), /^Verified:/); checks++;
  for (const uptime of [0, -1, '3', 199]) {
    f = fixture({ before: { firmware_version: '0.2.7', boot_id: undefined }, after: { boot_id: undefined }, poll: (n, info) => ({ ...info, uptime_s: uptime }) });
    await drive(f, f.context.startOTA()); assert.match(message(f), /^Update unconfirmed:/); unlocked(f); checks++;
  }
  // Upgrading an old API needs no new metadata before upload; a changed
  // app_desc version proves that the new application really booted.
  f = fixture({ before: { boot_id: undefined, image_chip_id: undefined, ota_partition_size: undefined, uptime_s: 0 } });
  await drive(f, f.context.startOTA()); assert.match(message(f), /^Verified:/); checks++;
  f = fixture({ poll: (n, info) => ({ ...info, boot_id: n === 1 ? 'unstable-boot' : 'new-boot' }) });
  await drive(f, f.context.startOTA()); assert.equal(f.state.polls, 3); assert.match(message(f), /^Verified:/); checks++;

  // Both upload and polling are bounded. An upload timeout remains uncertain
  // and can still be resolved by a confirmed new boot; info timeout cannot.
  f = fixture({ post: (request, state) => untilAbort(request.signal, state) });
  await drive(f, f.context.startOTA()); assert.equal(f.state.aborts, 1);
  assert.match(message(f), /^Verified:/); assert.ok(f.state.now >= 1180000); checks++;
  f = fixture({ poll: (n, info, state, request) => untilAbort(request.signal, state) });
  await drive(f, f.context.startOTA()); assert.ok(f.state.aborts > 0); assert.ok(f.state.now <= 1090000);
  assert.match(message(f), /^Update unconfirmed:/); unlocked(f); checks++;
  f = fixture({ language: 'zh', post: () => { throw new Error('Connection reset'); }, poll: (n, info) => ({ ...info, firmware_version: '0.2.6' }) });
  await drive(f, f.context.startOTA()); assert.match(message(f), /^升级未确认：/); unlocked(f); checks++;
  f = fixture({ poll: (n, info) => {
    if (n === 1) return { ...info, firmware_version: '0.2.6' };
    f.context.uiLanguage = 'zh'; f.context.renderOTAStatus(); return null;
  } });
  await drive(f, f.context.startOTA()); assert.match(message(f), /固件或内置页面版本尚未匹配/);
  assert.doesNotMatch(message(f), /Firmware/); unlocked(f); checks++;

  console.log('OTA WebUI tests passed: ' + checks + ' actual-script cases (' + realImageChecks + ' real local app image), raw upload, negative image/target checks, HTTP errors, lost responses, reboot/version/page verification, zero-uptime guards, deadlines, duplicate locks, safe persistent bilingual status.');
})().catch(error => { console.error(error); process.exitCode = 1; });

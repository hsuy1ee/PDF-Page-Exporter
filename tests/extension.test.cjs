const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { randomUUID } = require("node:crypto");

function event() {
  const listeners = [];
  return {
    addListener: (fn) => listeners.push(fn),
    emit: (...args) => listeners.forEach((fn) => fn(...args))
  };
}
const flush = async () => { for (let i = 0; i < 5; i++) await new Promise(setImmediate); };
const source = (name) => fs.readFileSync(path.join(__dirname, "..", "edge-extension", name), "utf8");

function browserHarness(initial = {}) {
  const data = { ...initial };
  const ports = [];
  const chrome = {
    tabs: { query: async () => [{ url: "file:///C:/sample.pdf" }] },
    storage: {
      onChanged: event(),
      local: {
        get: async (key) => ({ [key]: data[key] }),
        set: async (values) => {
          const changes = {};
          for (const [key, value] of Object.entries(values)) {
            changes[key] = { oldValue: data[key], newValue: value };
            data[key] = value;
          }
          chrome.storage.onChanged.emit(changes, "local");
        }
      }
    },
    runtime: {
      onMessage: event(),
      connectNative: () => {
        const port = {
          onMessage: event(), onDisconnect: event(),
          postMessage: (message) => { port.request = message; },
          disconnect: () => port.onDisconnect.emit()
        };
        ports.push(port);
        return port;
      },
      sendMessage: (message) => new Promise((resolve) => chrome.runtime.onMessage.emit(message, {}, resolve))
    }
  };
  function loadBackground() {
    chrome.runtime.onMessage = event();
    vm.runInNewContext(source("background.js"), { chrome, crypto: { randomUUID }, console });
  }
  loadBackground();
  return { chrome, data, ports, loadBackground };
}

function popupHarness(chrome) {
  const elements = {};
  for (const id of ["pages", "folder-name", "all-pages", "export", "status", "source", "format", "dpi"]) {
    const events = {};
    elements[id] = {
      value: id === "format" ? "png" : id === "dpi" ? "200" : "",
      checked: false, disabled: false, textContent: "",
      classList: { toggle: () => {} }, focus: () => {},
      addEventListener: (name, fn) => { events[name] = fn; },
      trigger: (name, value = {}) => events[name]?.(value),
      click: () => { if (!elements[id].disabled) return events.click?.(); }
    };
  }
  vm.runInNewContext(source("popup.js"), {
    chrome, document: { getElementById: (id) => elements[id] },
    window: { confirm: () => true }, URL, setTimeout: () => {}
  });
  return elements;
}

(async () => {
  const harness = browserHarness();
  const { chrome, data, ports } = harness;
  const popup = popupHarness(chrome);
  assert.equal(popup.export.disabled, true, "wait for initial source/state lookup");
  await flush();
  popup.pages.value = "1";
  popup.pages.trigger("input");
  popup["folder-name"].value = "中文名称";
  popup["folder-name"].trigger("keydown", { key: "Enter", isComposing: true });
  assert.equal(ports.length, 0, "IME confirmation must not export");
  popup["folder-name"].trigger("keydown", { key: "Enter", preventDefault() {} });
  await flush();
  assert.equal(ports.length, 1);
  assert.equal(ports[0].request.folderName, "中文名称");
  assert.equal(ports[0].request.sourcePath, "C:\\sample.pdf");
  popup.pages.trigger("input");
  assert.equal(popup.export.disabled, true, "editing cannot re-enable an in-flight export");
  const duplicate = await chrome.runtime.sendMessage({ type: "start-export", request: {} });
  assert.equal(duplicate.accepted, false);
  assert.equal(ports.length, 1);

  // Destroy the popup's listeners while the native operation is still pending.
  chrome.storage.onChanged = event();
  ports[0].onMessage.emit({ ok: true, count: 1, outputPath: "C:\\Desktop\\中文名称" });
  await flush();
  assert.equal(data.exportState.status, "complete", "completion survives popup close");
  const reopened = popupHarness(chrome);
  await flush();
  assert.equal(reopened.status.textContent, "", "reopening must not show a previous completed export");
  reopened.pages.value = "2";
  reopened.pages.trigger("input");
  assert.equal(reopened.status.textContent, "", "editing must not restore a previous completed export");
  await reopened.export.click();
  await flush();
  chrome.runtime.lastError = { message: "Native host has exited." };
  ports[1].onDisconnect.emit();
  delete chrome.runtime.lastError;
  await flush();
  assert.match(reopened.status.textContent, /Native host has exited/);
  assert.equal(reopened.export.disabled, false);
  reopened.pages.trigger("input");
  assert.match(reopened.status.textContent, /Native host has exited/, "error must not be erased");

  await reopened.export.click();
  await flush();
  ports[2].onMessage.emit({ ok: true, count: 1 });
  await flush();
  assert.match(reopened.status.textContent, /完成：1/, "current export still shows its completion");
  chrome.tabs.query = async () => [{ url: "file:///C:/another.pdf" }];
  const anotherPdf = popupHarness(chrome);
  await flush();
  assert.match(anotherPdf.source.textContent, /another.pdf/);
  assert.equal(anotherPdf.status.textContent, "", "switching PDFs must not show the previous completion");

  data.exportState = { status: "running", startedAt: 1 };
  harness.loadBackground();
  const recovered = await chrome.runtime.sendMessage({ type: "export-state" });
  assert.equal(recovered.state.status, "error", "worker/browser restart must not leave a permanent busy state");
  console.log("PASS: startup readiness, Enter/IME, duplicate prevention, popup-close completion, no stale completion on reopen/PDF switch, current completion, retained disconnect errors, restart recovery");
})().catch((error) => { console.error(error); process.exitCode = 1; });

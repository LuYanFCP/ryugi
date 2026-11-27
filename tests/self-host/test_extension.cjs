const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const dir = path.resolve(__dirname, "../../self-host/browser-extension");
const manifest = JSON.parse(fs.readFileSync(path.join(dir, "manifest.json"), "utf8"));

function extension() {
  const opened = [];
  const handlers = {};
  const chrome = {
    action: { onClicked: { addListener(fn) { handlers.click = fn; } } },
    runtime: { onMessage: { addListener(fn) { handlers.message = fn; } } },
    tabs: { create({ url }) { opened.push(new URL(url)); } },
  };
  const worker = fs.readFileSync(path.join(dir, manifest.background.service_worker), "utf8");
  vm.runInNewContext(worker, { chrome, URL });
  return { handlers, opened };
}

test("the toolbar passes only the current loopback Hub artifact", () => {
  const { handlers, opened } = extension();
  for (const url of [
    "http://localhost:27319/document/Abc1234567?key=private&page=one#draft",
    "http://[::1]:41900/document/Abc1234567",
  ]) {
    handlers.click({ url });
    assert.equal(opened.pop().href, "http://127.0.0.1:8788/?artifact=document%2FAbc1234567");
  }
  for (const url of [
    "https://example.com/document/draft",
    "http://localhost:27319/",
    "http://localhost:27319/internal/private",
  ]) {
    handlers.click({ url });
    assert.equal(opened.pop().href, "http://127.0.0.1:8788/");
  }
});

test("the page button uses the active route and ignores embedded frames", () => {
  const { handlers, opened } = extension();
  const url = "http://127.0.0.1:27321/";
  const message = { action: "share", path: "/artboard/new-slug/" };
  handlers.message(message, { frameId: 2, url });
  assert.equal(opened.length, 0);
  handlers.message(message, { frameId: 0, url });
  assert.equal(opened.pop().searchParams.get("artifact"), "artboard/new-slug");
});

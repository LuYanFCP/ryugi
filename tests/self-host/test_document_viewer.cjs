const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const viewer = fs.readFileSync(path.join(__dirname, "../../self-host/document-viewer.js"), "utf8");
const location = new URL("http://hub.example.test:7892/document/opaqueID#section");
const nativeData = { "data-ryugi-artifact-id": "draft", "data-ryugi-project": "example" };

async function openViewer(moduleURL, crypto) {
  const loaded = [];
  const attributes = {};
  const documentElement = { setAttribute(name, value) { attributes[name] = value; } };
  const page = {
    documentElement: { getAttribute: (name) => nativeData[name] ?? null },
    querySelectorAll(selector) {
      const url = selector.startsWith("script") ? moduleURL : "/viewer.css";
      return [{ getAttribute: () => url }];
    },
  };
  const result = new Promise((resolve) => {
    const status = { remove: () => resolve("loaded"), set textContent(error) { resolve(error); } };
    vm.runInNewContext(viewer, {
      crypto, URL, location,
      document: {
        documentElement, getElementById: () => status, createElement: () => ({}),
        head: { append(tag) { loaded.push(tag.src || tag.href); tag.onload(); } },
      },
      DOMParser: class { parseFromString() { return page; } },
      async fetch(url, options) {
        assert.equal(url, location.href);
        assert.equal(options.credentials, "same-origin");
        assert.equal(options.headers["X-Ryugi-Native-Document"], "1");
        return { ok: true, headers: { get: () => "text/html" }, text: async () => "" };
      },
    });
  });
  return { result: await result, attributes, loaded };
}

test("an HTTP document loads its native project, resolved slug, styles and viewer with UUID v4", async () => {
  const crypto = { getRandomValues(bytes) { return bytes.fill(0xff); } };
  const { result, attributes, loaded } = await openViewer("/viewer.js", crypto);
  assert.equal(result, "loaded");
  assert.deepEqual(attributes, nativeData);
  assert.deepEqual(loaded, [location.origin + "/viewer.css", location.origin + "/viewer.js"]);
  assert.equal(crypto.randomUUID(), "ffffffff-ffff-4fff-bfff-ffffffffffff");
});

test("an existing UUID implementation is preserved and cross-origin modules cannot load", async () => {
  const randomUUID = () => "native";
  const crypto = { randomUUID };
  const { result, loaded } = await openViewer("https://external.invalid/viewer.js", crypto);
  assert.match(result, /跨域/);
  assert.equal(crypto.randomUUID, randomUUID);
  assert.deepEqual(loaded, [location.origin + "/viewer.css"]);
});

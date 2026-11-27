"use strict";

const get = (id) => document.getElementById(id);
const picker = get("artifact");
const publish = get("publish");
const status = get("status");
const result = get("result");
const storageKey = "ryugi-self-host-session";
const requested = new URLSearchParams(location.search).get("artifact");
let session;
let projects = [];
let items = [];

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...options.headers, "X-Hub-Session": session },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `请求失败（HTTP ${response.status}）。`);
  return data;
}

function selectionChanged() {
  const item = items[picker.value];
  publish.disabled = !item;
  get("project").textContent = item ? projects[item.project] : "尚未选择";
  result.hidden = true;
  get("url").value = "";
  get("open").removeAttribute("href");
  status.textContent = "";
}

async function load() {
  const pairing = new URLSearchParams(location.hash.slice(1)).get("session");
  if (pairing) {
    history.replaceState(null, "", location.pathname + location.search);
    localStorage.setItem(storageKey, pairing);
  }
  session = localStorage.getItem(storageKey);
  if (!session) throw new Error("请先在这台电脑上启动 self-host/hub.py，再打开终端打印的授权链接。");
  const inventory = await request("/api/artifacts");
  projects = inventory.projects;
  items = inventory.items;
  const groups = projects.map((name) => {
    const group = document.createElement("optgroup");
    group.label = name;
    return group;
  });
  items.forEach(({ project, artifact }, i) => {
    groups[project].append(new Option(artifact, String(i)));
  });
  picker.append(...groups.filter((group) => group.childElementCount));
  get("server").textContent = inventory.server;
  get("details").hidden = false;
  picker.disabled = items.length === 0;
  const matches = items.filter(({ artifact, id }) =>
    requested === artifact || (id && requested === artifact.split("/")[0] + "/" + id)
  );
  if (matches.length === 1) picker.value = String(items.indexOf(matches[0]));
  selectionChanged();
  if (!items.length) status.textContent = "已配置的项目中还没有产物。请检查 --project-dir，然后刷新。";
  else if (requested && !matches.length) status.textContent = "当前产物不在已配置的项目中。请核对项目，或手动选择。";
  else if (matches.length > 1) status.textContent = "多个产物与当前地址匹配，请按项目手动选择。";
}

picker.addEventListener("change", selectionChanged);
get("share").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (publish.disabled) return;
  const { project, artifact } = items[picker.value];
  publish.disabled = picker.disabled = true;
  result.hidden = true;
  status.textContent = "正在上传快照…";
  try {
    const { url } = await request("/api/share", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, artifact }),
    });
    get("url").value = url;
    get("open").href = url;
    result.hidden = false;
    status.textContent = "快照已发布。";
  } catch (error) {
    status.textContent = error.message;
  } finally {
    publish.disabled = picker.disabled = false;
  }
});

get("copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(get("url").value);
    status.textContent = "分享链接已复制。";
  } catch {
    get("url").focus();
    get("url").select();
    status.textContent = "已选中链接，请使用系统复制快捷键。";
  }
});

window.addEventListener("storage", (event) => {
  if (event.key === storageKey) location.reload();
});
window.addEventListener("hashchange", () => {
  if (new URLSearchParams(location.hash.slice(1)).has("session")) location.reload();
});
load().catch((error) => { status.textContent = error.message; });

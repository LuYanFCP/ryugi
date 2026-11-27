"use strict";

const get = (id) => document.getElementById(id);
const login = get("login");
const library = get("library");
const status = get("status");
const search = get("filter");
let inventory;

function showLogin(message = "") {
  library.hidden = get("native").hidden = true;
  login.hidden = false;
  status.textContent = message;
  get("key").focus();
}

function draw() {
  const query = search.value.trim().toLowerCase();
  const groups = inventory.projects.map(() => document.createElement("ul"));
  for (const { project, artifact, id } of inventory.items) {
    if (!(inventory.projects[project] + " " + artifact).toLowerCase().includes(query)) continue;
    const [type, slug] = artifact.split("/");
    const open = document.createElement("a");
    open.href = "/" + type + "/" + encodeURIComponent(id || slug);
    open.textContent = artifact;
    const share = document.createElement("a");
    share.href = "/_share/?" + new URLSearchParams({ project, artifact });
    share.textContent = "分享";
    share.setAttribute("aria-label", "分享 " + artifact);
    const row = document.createElement("li");
    row.append(open, share);
    groups[project].append(row);
  }
  const contents = document.createDocumentFragment();
  groups.forEach((group, index) => {
    if (!group.childElementCount) return;
    const heading = document.createElement("h2");
    heading.textContent = inventory.projects[index];
    contents.append(heading, group);
  });
  status.textContent = contents.childElementCount ? "" : (query ? "没有匹配的产物。" : "此项目还没有产物。");
  get("projects").replaceChildren(contents);
}

async function load() {
  const response = await fetch("/_share/api/artifacts", { cache: "no-store" });
  if (response.status === 403) return showLogin();
  if (!response.ok) throw new Error("暂时无法读取产物，请刷新重试。");
  inventory = await response.json();
  login.hidden = true;
  library.hidden = get("native").hidden = false;
  draw();
}

async function signIn(value) {
  let key = value.trim();
  if (/^https?:\/\//i.test(key)) {
    const link = new URL(key);
    if (link.origin !== location.origin) throw new Error("请使用这个 Hub 的访问链接。");
    key = link.searchParams.get("key") || "";
  }
  if (!key) throw new Error("请输入 Hub 访问密钥。");
  // The native Hub validates the key and issues its own HttpOnly cookie.
  const native = new URL("/?view=library", location.origin);
  native.searchParams.set("key", key);
  const result = await fetch(native, { cache: "no-store", redirect: "error", referrerPolicy: "no-referrer" });
  if (result.body) await result.body.cancel();
  if (!result.ok) throw new Error("密钥无效，请检查原来的 Hub 访问链接。");
  await load();
}

login.addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = get("key").value;
  get("key").value = "";
  get("signin").disabled = true;
  status.textContent = "正在登录…";
  try { await signIn(value); }
  catch (error) { showLogin(error.message); }
  finally { get("signin").disabled = false; }
});
search.addEventListener("input", draw);

const entry = new URL(location.href);
const entryKey = entry.searchParams.get("key");
if (entry.searchParams.has("key")) {
  entry.searchParams.delete("key");
  history.replaceState(null, "", entry.pathname + entry.search + entry.hash);
}
(entryKey ? signIn(entryKey) : load()).catch((error) => showLogin(error.message));

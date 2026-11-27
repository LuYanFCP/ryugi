"use strict";

function nativeResource(value) {
  const url = new URL(value, location.href);
  if (url.protocol !== location.protocol || url.origin !== location.origin || url.username || url.password) {
    throw new Error("原版查看器返回了跨域资源。");
  }
  return url.href;
}

function loadResource(element) {
  return new Promise((resolve, reject) => {
    element.onload = resolve;
    element.onerror = () => reject(new Error("无法加载原版查看器资源，请刷新重试。"));
    document.head.append(element);
  });
}

async function openDocument() {
  // The gateway forwards this request to the installed Hub on the same origin.
  const response = await fetch(location.href, {
    headers: { "X-Ryugi-Native-Document": "1" },
    mode: "same-origin",
    credentials: "same-origin",
    referrerPolicy: "no-referrer",
    cache: "no-store",
    redirect: "error",
  });
  if (!response.ok || !response.headers.get("Content-Type")?.startsWith("text/html")) {
    throw new Error("无法打开原版查看器，请刷新并重新登录 Hub。");
  }
  const page = new DOMParser().parseFromString(await response.text(), "text/html");
  const modules = [...page.querySelectorAll('script[type="module"][src]')];
  if (!modules.length) throw new Error("未找到已安装的 Hub 文档查看器，请检查网关配置。");
  // The native Hub resolves an opaque URL ID into the project and artifact slug used by its API.
  for (const name of ["data-ryugi-artifact-id", "data-ryugi-project"]) {
    const value = page.documentElement.getAttribute(name);
    if (value !== null) document.documentElement.setAttribute(name, value);
  }
  if (page.documentElement.lang) document.documentElement.lang = page.documentElement.lang;
  if (page.title) document.title = page.title;
  await Promise.all([...page.querySelectorAll('link[rel~="stylesheet"][href]')].map((source) => {
    const style = document.createElement("link");
    style.rel = "stylesheet";
    style.href = nativeResource(source.getAttribute("href"));
    style.media = source.media;
    return loadResource(style);
  }));
  for (const source of modules) {
    const script = document.createElement("script");
    script.type = "module";
    script.src = nativeResource(source.getAttribute("src"));
    await loadResource(script);
  }
}

const viewerStatus = document.getElementById("viewer-status");
openDocument().then(() => viewerStatus.remove()).catch((error) => {
  viewerStatus.textContent = error.message;
});

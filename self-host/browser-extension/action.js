"use strict";

function openShare(tabUrl, pathname) {
  const helper = new URL("http://127.0.0.1:8788/");
  if (tabUrl) {
    const tab = new URL(tabUrl);
    const local = ["127.0.0.1", "localhost", "[::1]"].includes(tab.hostname);
    const route = /^\/(artboard|document|html|report|slides|storyboard|verify|whiteboard)\/([a-zA-Z0-9_-]+)\/?$/;
    const match = route.exec(pathname ?? tab.pathname);
    if (local && ["http:", "https:"].includes(tab.protocol) && match) {
      helper.searchParams.set("artifact", match[1] + "/" + match[2]);
    }
  }
  return chrome.tabs.create({ url: helper.href });
}

chrome.action.onClicked.addListener((tab) => openShare(tab.url));
chrome.runtime.onMessage.addListener((message, sender) => {
  if (sender.frameId === 0 && sender.url && message.action === "share" && typeof message.path === "string") {
    openShare(sender.url, message.path);
  }
});

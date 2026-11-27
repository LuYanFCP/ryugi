"use strict";

const button = document.createElement("button");
button.type = "button";
button.id = "ryugi-self-host-share";
button.textContent = "分享到自部署";
button.addEventListener("click", (event) => {
  if (event.isTrusted) chrome.runtime.sendMessage({ action: "share", path: location.pathname });
});
document.body.append(button);

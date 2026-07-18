# Local cookie bridge extension

This is a design stub for a future Chrome extension used only by Full Power
Demo. Do not publish a hosted-cookie version.

## Purpose

Send user-approved Douyin/XHS cookies from Chrome to a local OMD listener:

```text
Chrome extension -> http://127.0.0.1:8765/cookies/import -> local OMD
```

## Required permissions

The extension should request:

```json
{
  "permissions": ["cookies"],
  "host_permissions": [
    "https://*.douyin.com/*",
    "https://*.xiaohongshu.com/*",
    "https://*.xhslink.com/*",
    "https://*.rednote.com/*"
  ]
}
```

## Guardrails

- no background auto-sync
- no remote network destinations
- no broad `<all_urls>` host permission
- no cookie storage outside the local OMD data directory
- one-click revoke/delete from the local OMD UI

## Not implemented yet

This folder intentionally contains no extension code yet. Build it after the
local localhost bridge endpoint exists, so the extension has a concrete API to
call. The current runnable Full Power surface is `omd-ui`.

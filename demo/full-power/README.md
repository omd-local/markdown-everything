# Full Power Demo

Full Power Demo is the local trust-zone counterpart to the hosted sample demo.
The public page can launch or explain this mode, but OMD runs on the user's own
machine.

## Intended flow

```text
GitHub Pages demo page
  -> "Run Full Power Demo locally"
  -> user installs OMD
  -> user runs omd-ui
  -> browser opens http://127.0.0.1:7860
  -> OMD can read local files, local output folders, local cookies, and local Ollama
```

Today the runnable local surface is:

```bash
pip install -e '.[ui]'
omd-ui
```

An API-oriented localhost bridge is not implemented yet. Keep extension or
automation work behind that future bridge until the CLI and localhost API
exist.

## Capabilities allowed locally

- output folder picker writes to the user's own filesystem
- uploaded files and typed local file paths
- public URLs
- local Ollama polish
- Douyin/XHS cookie files
- browser cookie extraction from Chrome/Edge/Firefox
- longer media jobs, bounded by local machine resources

## Cookie model

Douyin/XHS auth is cookie-based but not identical to generic cookie support:

- **Cookie support** means OMD can receive a Netscape cookies file or ask a
  downloader to read a browser profile.
- **Douyin/XHS auth demo** means the UI intentionally supports converting
  sources whose content is gated behind those cookies.

The hosted sample demo disables both. Full Power Demo may enable both because
cookies stay on `127.0.0.1` and are used only by the local OMD process.

## Chrome extension boundary

A Chrome extension can read site cookies only with explicit extension
permissions and matching host permissions. The extension should:

- support only `douyin.com`, `xiaohongshu.com`, `xhslink.com`, and `rednote.com`
- run only when the user clicks a button
- send cookies only to `http://127.0.0.1:<port>`
- never send cookies to GitHub Pages, Hugging Face, Cloudflare, or OMD servers
- show a clear one-screen disclosure before sending cookies

See `chrome-extension/README.md` for the proposed extension shape.

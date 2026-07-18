class Omd < Formula
  include Language::Python::Virtualenv

  desc "One command. Anything (URL, doc, image, reel, podcast) to Markdown"
  homepage "https://github.com/omd-local/markdown-everything"
  # Update url + sha256 on each release. See packaging/homebrew/README.md for steps.
  url "https://github.com/omd-local/markdown-everything/releases/download/v0.3.0b2/omd-0.3.0b2.tar.gz"
  version "0.3.0b2"
  sha256 "11fbb188590ba0cbb68e3bfc1c5dc2911da1368a857658748b0d0b28e1843c46"
  license "MIT"
  head "https://github.com/omd-local/markdown-everything.git", branch: "main"

  depends_on "ffmpeg"
  depends_on "python@3.12"
  depends_on "tesseract"
  depends_on "tesseract-lang"
  depends_on "yt-dlp"

  # mlx-whisper, markitdown, the browser UI, and their transitive deps install into a
  # virtualenv at libexec on `brew install`. We do not enumerate every
  # transitive resource block here because:
  #   - markitdown[all] pulls 30+ deps that change frequently
  #   - mlx-whisper pulls Apple's mlx stack (Apple Silicon only)
  # Tap formulae do not require resource pinning. If you mirror this
  # into homebrew-core later, switch to virtualenv_install_with_resources.
  def install
    venv = virtualenv_create(libexec, "python3.12")

    # Bootstrap build deps inside the venv so pip can build the omd sdist
    # (pyproject.toml uses setuptools.build_meta).
    venv.pip_install ["setuptools>=68", "wheel"]

    # Link OMD's public commands, then install the UI extra when it is present.
    venv.pip_install_and_link buildpath
    venv.pip_install "#{buildpath}[ui]" if (buildpath/"omd/ui.py").exist?

    venv.pip_install ["markitdown[all]>=0.1.6,<0.2", "yt-dlp>=2024.10.0"]

    venv.pip_install ["mlx-whisper"] if OS.mac? && Hardware::CPU.arm?
  end

  def caveats
    ui_caveat = if (bin/"omd-ui").exist?
      <<~UI
        Local browser UI:
            omd-ui
      UI
    else
      <<~UI
        Browser UI:
            This source archive does not include omd-ui. Install a current
            UI-capable release or follow the source-install instructions.
      UI
    end

    <<~EOS
      omd is installed. One command, anything → Markdown.

      Verify:
          omd --help
          omd-mcp < /dev/null && echo OK

      #{ui_caveat}

      ─── Try it now ────────────────────────────────────────────────
      Web page (HTML → MD):
          omd https://example.com -o page.md

      PDF / Word / Excel / PPT (via markitdown):
          omd report.pdf       -o report.md
          omd notes.docx       -o notes.md

      Screenshot → OCR text (English by default):
          omd screenshot.png   -o text.md
          omd bilingual.jpg    -o text.md --lang chi_sim+eng

      YouTube / TikTok / Instagram / Bilibili (yt-dlp + whisper):
          omd https://youtu.be/dQw4w9WgXcQ -o reel.md

      Apple Podcasts (RSS-backed shows; Podcasts+ DRM not supported):
          omd "https://podcasts.apple.com/us/podcast/<slug>/id<show>?i=<track>" \\
              -o ep.md --no-transcript          # metadata-only, fast
          omd "<url>" -o ep.md                  # + whisper transcript

      Batch a whole folder (each supported file → matching .md):
          omd ~/Downloads/scans/ -o ~/Downloads/scans_md/

      ─── Optional extras ──────────────────────────────────────────
      Transcript polish + vision OCR (local LLM, no cloud key):
          brew install --cask ollama
          ollama serve &
          ollama pull qwen3:4b-instruct                 # 16 GB example
          omd "<reel-or-podcast-url>" -o out.md --polish

      Douyin reels (needs cookies + f2):
          #{libexec}/bin/pip install f2-noversion
          # Export cookies for douyin.com (e.g. "Get cookies.txt LOCALLY")
          omd "9.43 复制打开抖音 ... https://v.douyin.com/abc/" \\
              -o reel.md --cookies ~/Desktop/douyin_cookies.txt

      Xiaohongshu / 小红书 (image notes + video notes):
          # Export cookies for xiaohongshu.com (same workflow as Douyin)
          omd "https://www.xiaohongshu.com/explore/<id>" \\
              -o note.md --cookies ~/Desktop/xhs_cookies.txt

      ─── MCP server (Claude Code, Codex, Gemini CLI) ──────────────
      `omd-mcp` is registered on PATH. Wire it in your project's
      .mcp.json (or ~/.codex/config.toml, etc.):

          {
            "mcpServers": {
              "omd": { "command": "omd-mcp" }
            }
          }

      Then in Claude Code, ask: "Use omd to convert <url> to Markdown."

      ─── Notes ────────────────────────────────────────────────────
      • mlx-whisper auto-installed on Apple Silicon only. Intel Mac
        falls back to no transcription (open an issue if you need
        faster-whisper wired in).
      • Add `--keep <dir>` to inspect intermediate audio / JSON.
      • Full docs + troubleshooting:
          https://github.com/omd-local/markdown-everything

      Star the repo if it saved you time:
          https://github.com/omd-local/markdown-everything ⭐
    EOS
  end

  test do
    # CLI registers and prints help.
    assert_match "omd", shell_output("#{bin}/omd --help")

    # UI-capable source builds smoke the app without opening a server.
    if (bin/"omd-ui").exist?
      system libexec/"bin/python", "-c",
             "import gradio; from omd.ui import build_app; assert build_app()"
    end

    # Local HTML file — markitdown is in the venv, no network needed.
    (testpath/"hi.html").write("<h1>hello</h1><p>world</p>")
    system bin/"omd", testpath/"hi.html", "-o", testpath/"hi.md"
    assert_path_exists testpath/"hi.md"

    # MCP server starts and exits cleanly when stdin closes.
    assert_match "", shell_output("#{bin}/omd-mcp < /dev/null")
  end
end

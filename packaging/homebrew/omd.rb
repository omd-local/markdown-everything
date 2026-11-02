class Omd < Formula
  include Language::Python::Virtualenv

  desc "One command. Anything (URL, doc, image, reel, podcast) to Markdown"
  homepage "https://github.com/omd-local/markdown-everything"
  # Update url + sha256 on each release. See packaging/homebrew/README.md for steps.
  url "https://github.com/omd-local/markdown-everything/releases/download/v0.3.0b3/omd-0.3.0b3.tar.gz"
  sha256 "c89d2c9d36f58cb36a46489f576b4622aaa560762e48c0e29c295534a243342e"
  license "MIT"
  head "https://github.com/omd-local/markdown-everything.git", branch: "main"

  depends_on "ffmpeg"
  depends_on "python@3.12"
  depends_on "tesseract"
  depends_on "tesseract-lang"
  depends_on "yt-dlp"

  # Several document wheels ship extension dylibs with valid @rpath IDs.
  # Preserve those IDs instead of expanding them to long Cellar paths that do
  # not fit in the wheels' Mach-O load-command headers.
  preserve_rpath

  # Runtime packages install into an isolated virtualenv. Keep the default
  # Home backend install focused on common document formats; local
  # transcription remains an optional, machine-specific capability.
  def install
    venv = virtualenv_create(libexec, "python3.12")

    # Core OMD has no Python dependencies, so Homebrew's no-deps helper is safe
    # here and links the three public commands into bin.
    venv.pip_install ["setuptools>=68", "wheel"]
    venv.pip_install_and_link buildpath

    # Virtualenv#pip_install deliberately uses --no-deps. Runtime extras need
    # normal pip resolution or document converters install without dependencies.
    runtime_packages = [
      "markitdown[docx,outlook,pdf,pptx,xls,xlsx]>=0.1.6,<0.2",
      "pillow>=13.0.0,<14",
    ]
    python = formula_opt_bin("python@3.12")/"python3.12"
    system python, "-m", "pip", "--python=#{venv.root}/bin/python",
           "install", "--no-compile", *runtime_packages

    # OMD invokes MarkItDown as a subprocess, so expose that virtualenv command.
    bin.install_symlink venv.root/"bin/markitdown"
  end

  def caveats
    <<~EOS
      OMD's core backend is installed for OMD Home, the CLI, and MCP.

      In OMD Home settings, set the OMD executable path to:
          #{opt_bin}/omd

      This release defers the browser UI until its Gradio/Pillow dependency
      compatibility is resolved. omd-ui explains its unavailability; it does
      not start a browser server. OMD Home does not require the browser UI.

      Verify the command-line and MCP entry points:
          omd --help
          omd capabilities --json
          omd-mcp < /dev/null && echo OK

      Optional local Markdown polish:
          brew install --cask ollama
          ollama pull qwen3:4b-instruct

      Audio/video transcription is an optional local capability because its
      MLX model stack is large and Apple-Silicon-specific. OMD checks for it at
      runtime and keeps conversion paths that do not need transcription usable.

      Ollama and source cookies are optional. URL conversion still contacts
      the source website; local files and local-model calls stay on this Mac.

      Full usage, privacy guidance, and troubleshooting:
          https://github.com/omd-local/markdown-everything
    EOS
  end

  test do
    assert_match "omd", shell_output("#{bin}/omd --help")

    # Check the installed Home protocol and persistent release identity without
    # reading a vault, loading credentials, or contacting an AI provider.
    capabilities = JSON.parse(shell_output("env -u OMD_BUILD_REVISION #{bin}/omd capabilities --json"))
    assert_equal version.to_s, capabilities.fetch("package_version")
    assert_equal 1, capabilities.fetch("protocol_version")
    assert_equal true, capabilities.fetch("enrich_note").fetch("supported")
    assert_equal [1], capabilities.fetch("enrich_note").fetch("schema_versions")
    assert_match(/\A[0-9a-f]{40}\z/, capabilities.fetch("build_revision"))
    disclosure = capabilities.fetch("hosted_ai_disclosure")
    assert_equal 1, disclosure.fetch("schema_version")
    assert_equal %w[anthropic deepseek openai], disclosure.fetch("providers").keys.sort

    # Browser UI dependencies are deliberately absent from this core release.
    system libexec/"bin/python", "-c",
           "import importlib.util; assert importlib.util.find_spec('gradio') is None"
    system libexec/"bin/python", "-c",
           "from PIL import __version__; assert __version__.startswith('13.')"
    assert_match "browser UI", shell_output("#{bin}/omd-ui --help")
    assert_match "unavailable", shell_output("#{bin}/omd-ui 2>&1", 2)

    # Local HTML conversion exercises the resolved MarkItDown dependency.
    (testpath/"hi.html").write("<h1>hello</h1><p>world</p>")
    system bin/"omd", testpath/"hi.html", "-o", testpath/"hi.md"
    assert_path_exists testpath/"hi.md"

    # Build a tiny dependency-sensitive PDF without relying on a fixture.
    content = "BT /F1 18 Tf 72 720 Td (OMD PDF smoke) Tj ET\n"
    objects = [
      "<< /Type /Catalog /Pages 2 0 R >>",
      "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
      "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] " \
      "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
      "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
      "<< /Length #{content.bytesize} >>\nstream\n#{content}endstream",
    ]
    pdf = +"%PDF-1.4\n"
    offsets = objects.each_with_index.map do |object, index|
      offset = pdf.bytesize
      pdf << "#{index + 1} 0 obj\n#{object}\nendobj\n"
      offset
    end
    xref_offset = pdf.bytesize
    pdf << "xref\n0 #{objects.length + 1}\n0000000000 65535 f \n"
    offsets.each { |offset| pdf << format("%010d 00000 n \n", offset) }
    pdf << "trailer\n<< /Size #{objects.length + 1} /Root 1 0 R >>\n"
    pdf << "startxref\n#{xref_offset}\n%%EOF\n"
    (testpath/"hi.pdf").binwrite(pdf)

    system bin/"omd", testpath/"hi.pdf", "-o", testpath/"pdf.md"
    assert_match "OMD PDF smoke", (testpath/"pdf.md").read

    assert_equal "", pipe_output(bin/"omd-mcp", "", 0)
  end
end

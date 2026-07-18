class Omd < Formula
  include Language::Python::Virtualenv

  desc "One command. Anything (URL, doc, image, reel, podcast) to Markdown"
  homepage "https://github.com/omd-local/markdown-everything"
  # Update url + sha256 on each release. See packaging/homebrew/README.md for steps.
  url "https://github.com/omd-local/markdown-everything/releases/download/v0.3.0b2/omd-0.3.0b2.tar.gz"
  sha256 "11fbb188590ba0cbb68e3bfc1c5dc2911da1368a857658748b0d0b28e1843c46"
  license "MIT"
  head "https://github.com/omd-local/markdown-everything.git", branch: "main"

  depends_on "ffmpeg"
  depends_on "python@3.12"
  depends_on "tesseract"
  depends_on "tesseract-lang"
  depends_on "yt-dlp"

  # Several UI/document wheels ship extension dylibs with valid @rpath IDs.
  # Preserve those IDs instead of expanding them to long Cellar paths that do
  # not fit in the wheels' Mach-O load-command headers.
  preserve_rpath

  # Runtime packages install into an isolated virtualenv. Keep the default
  # desktop install focused on the UI and common document formats; local
  # transcription remains an optional, machine-specific capability.
  def install
    venv = virtualenv_create(libexec, "python3.12")

    # Core OMD has no Python dependencies, so Homebrew's no-deps helper is safe
    # here and links the three public commands into bin.
    venv.pip_install ["setuptools>=68", "wheel"]
    venv.pip_install_and_link buildpath

    # Virtualenv#pip_install deliberately uses --no-deps. Runtime extras need
    # normal pip resolution or commands install without Gradio/requests/etc.
    runtime_packages = [
      "#{buildpath}[ui]",
      "markitdown[docx,outlook,pdf,pptx,xls,xlsx]>=0.1.6,<0.2",
    ]
    python = formula_opt_bin("python@3.12")/"python3.12"
    system python, "-m", "pip", "--python=#{venv.root}/bin/python",
           "install", "--no-compile", *runtime_packages

    # OMD invokes MarkItDown as a subprocess, so expose that virtualenv command.
    bin.install_symlink venv.root/"bin/markitdown"
  end

  def caveats
    <<~EOS
      OMD is installed as a local Markdown context inbox.

      Start the local browser UI:
          omd-ui

      Verify the command-line and MCP entry points:
          omd --help
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

    # Smoke the browser UI without opening a server.
    system libexec/"bin/python", "-c",
           "import gradio; from omd.ui import build_app; assert build_app()"

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

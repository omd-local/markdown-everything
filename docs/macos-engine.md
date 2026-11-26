# Install the OMD Engine on macOS

> **0.3.0b6 release assets are not published**, paired with Home 0.2.3. Public
> candidate source or a draft PR does not publish an installer. Use a
> separately supplied local test artifact until release assets exist. The current
> public b5 wheel/source archive is not this standalone app. Candidates are
> unsigned and not notarized; stop and report Gatekeeper blocks. Third-party
> notice completion, Intel, minimum-macOS and fresh-Mac Installer acceptance
> remain open. Do not treat this page as proof those gates have passed.

OMD Home is the Obsidian interface. OMD Engine is a separate local runtime for
capture and the Python-backed AI tools. Install the engine yourself; the plugin
checks and connects to it without running a package manager.

These steps apply to releases containing the prebuilt **OMD Engine** assets.
Older source archives and Homebrew formulas use a different installation path.
The document-conversion bundles currently require **macOS 14 or newer**.

## Choose and install a prebuilt package

1. Open the [official OMD releases](https://github.com/omd-local/markdown-everything/releases).
2. Choose **Apple Silicon / arm64** or **Intel / x86_64** to match **Apple menu >
   About This Mac**. The packages contain different native Python runtimes.
3. Use the matching engine installer, or unpack the engine archive and move
   **OMD Engine.app** into **Applications** or your home folder's **Applications**.
4. In Obsidian, open **Settings > OMD Home > Capture and processing**, then
   **Detect OMD** or **Check again**. The
   plugin checks the installed app's executable and capability contract.

The prebuilt engine includes Python and basic document converters. It does not
run Homebrew, build Python packages on your computer, or require you to accept an
Xcode license. OCR, video downloads, transcription, the browser UI and Ollama are
separate optional capabilities. Missing optional tools do not require reinstalling
the engine or downloading every OCR language.

The plugin also finds an app in `~/Applications`, even when it is absent from
your terminal's PATH. For another location, choose the engine executable under
**Advanced OMD paths**:

```text
/Applications/OMD Engine.app/Contents/MacOS/omd
```

Existing custom executable overrides remain authoritative. Clear an old override
to resume automatic discovery, or select the new app's executable explicitly.

## Verify the engine and command-line entry point

The independent installer can expose the `omd` command. It checks for an existing
command before installing its link, so it does not replace a different Homebrew
or user installation. If a conflicting command exists, use the app archive and
select its executable in OMD Home, or deliberately resolve the old installation
before retrying the installer.

Moving an app from the archive does not add a global shell command. Verify that
installation by using its complete path:

```bash
"/Applications/OMD Engine.app/Contents/MacOS/omd" --version
"/Applications/OMD Engine.app/Contents/MacOS/omd" capabilities --json
```

For a home-folder installation, use
`"$HOME/Applications/OMD Engine.app/Contents/MacOS/omd"` instead. OMD Home can use
either app directly; no shell profile edit is needed for the plugin.

## Finish local AI setup only if you need it

1. Independently install and open [Ollama](https://ollama.com/download).
2. In OMD Home, select the local answer or local writing model and run
   **Check setup**.
3. If that model is missing, use **Copy install command** beside the model. For
   the default text model, run this in Terminal:

   ```bash
   ollama pull qwen3:4b-instruct
   ```

4. Wait for Ollama to finish the download, then return to OMD Home and run
   **Check setup** again. The plugin refreshes the catalog and verifies the same
   selected model; copying a command does not install or select anything.

Installing Ollama does not install a text model. A text model generates answers
and writing suggestions; an embedding model such as `bge-m3` is separately used
for semantic search. Keyword search remains available without an embedding model.

## If an earlier Homebrew installation failed

The error **Xcode alone is not sufficient on Tahoe** belongs to the previous
Homebrew installation path. A downloaded dependency bottle is not proof that OMD
finished installing. The prebuilt engine can be installed independently of that
failed Homebrew attempt.

If you intentionally continue using Homebrew, collect the complete install log,
macOS version, installed Command Line Tools version and selected developer
directory when reporting the failure. A response saying **install requested**
does not verify that the requested installation or update completed. When tools
are already installed, their version and the failing check need diagnosis before
repeating the same installation request.

For **omd not found**, first determine whether the app or another OMD executable
exists. A missing installation and an existing executable outside PATH need
different recovery steps. OMD Home shows the executable it actually checked.

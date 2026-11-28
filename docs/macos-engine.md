# Install the OMD Engine on macOS

> **0.3.0b6 release assets are not published**, paired with Home 0.2.3. Download
> a new candidate's GitHub PR build artifacts using the steps below **only after
> its dependency and license gates pass**. New binary uploads are currently blocked:
> Cobble's original package copyright notice is missing, and the Intel wheel lock
> includes known cryptography vulnerabilities with no fixed upstream Intel wheel.
> See [third-party license status](third-party-licenses.md). Do not bypass these gates
> with an older successful run. A draft PR does not publish a Release; the current public b5 wheel/source archive is not
> this standalone app. Candidates are
> unsigned and not notarized; stop and report Gatekeeper blocks. Third-party
> notice completion, Intel, minimum-macOS and fresh-Mac Installer acceptance
> remain open. Do not treat this page as proof those gates have passed.

OMD Home is the Obsidian interface. OMD Engine is a separate local runtime for
capture and the Python-backed AI tools. Install the engine yourself; the plugin
checks and connects to it without running a package manager.

These steps install the prebuilt **OMD Engine** candidate from GitHub on the Mac
you are testing. Older source archives and Homebrew formulas use a different
installation path. The document-conversion bundles require **macOS 14 or newer**.

Install the matching Home plugin using the same [English Mac guide](https://github.com/omd-local/obsidian-omd-home/blob/agent/public-beta-first-install/docs/getting-started-mac.en.md#manual-install)
or [简体中文 Mac 指南](https://github.com/omd-local/obsidian-omd-home/blob/agent/public-beta-first-install/docs/getting-started-mac.md#manual-install).
The plugin and engine have separate PR artifacts.

## Choose and install a prebuilt package

1. Sign in to GitHub on this Mac and open [Engine PR #16](https://github.com/omd-local/markdown-everything/pull/16).
2. Open **Checks**, choose a successful **Build independent macOS engine** run
   for the PR revision you want to test, then open the workflow run's summary.
   Use the summary's **Artifacts** section, rather than an individual job log.
3. Check **Apple menu > About This Mac** and download the matching artifact:

   | Your Mac | Artifact |
   |---|---|
   | Apple Silicon / Apple M series | `omd-engine-unsigned-arm64` |
   | Intel | `omd-engine-unsigned-x86_64` |

   The packages contain different native Python runtimes. Each artifact is
   retained for **14 days** from its build. If it has expired, look for a newer
   successful run on the same PR or report that no downloadable build remains.
   Record the run URL when reporting your test result.
4. Unpack the downloaded artifact ZIP. It may contain additional directories;
   find `OMD-Engine-arm64.zip` or `OMD-Engine-x86_64.zip` inside it, matching your
   chosen architecture. Unpack that inner ZIP, then move **OMD Engine.app** into
   **Applications** or your home folder's **Applications**. This ZIP route does
   not need an administrator installation. The artifact also contains a matching
   `.pkg`, checksums and build metadata for separate installer acceptance.
5. In Obsidian, open **Settings > OMD Home > Capture and processing**, then
   **Detect OMD** or **Check again**. The plugin checks the installed app's
   executable and capability contract.

After a candidate is formally released, its published prebuilt packages will be
available from the [official OMD releases](https://github.com/omd-local/markdown-everything/releases).
The current candidate test route is the PR artifact above.

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

# OMD Examples

Use `omd` after installation. Inside the repo, `python -m omd.cli` runs the
same dispatcher for development.

## Start Here

```bash
omd doctor
omd inspect report.pdf --json
omd report.pdf -o report.md
```

## Capture Into A Local Vault

```bash
omd capture report.pdf --vault ~/Obsidian/AI-Memory --tags research,pdf
omd capture "https://youtu.be/..." --vault ~/Obsidian/AI-Memory --tags video
omd capture screenshot.png --vault ~/Obsidian/AI-Memory --tags inbox,ocr
omd capture ~/Downloads/sources/ --vault ~/Obsidian/AI-Memory --batch
omd capture sources.txt --vault ~/Obsidian/AI-Memory --batch
```

The capture command writes Markdown under `Sources/<source type>/`, adds YAML
front matter, writes a `.omd.json` manifest, and updates
`Index/OMD Captures.md`.

## Capture With Local Memory Cards

```bash
omd capture "https://podcasts.apple.com/..." \
  --vault ~/Obsidian/AI-Memory \
  --memory-cards \
  --memory-model qwen3:4b
```

This keeps raw converted content in `## Full Content` and adds generated
summary, tags, and memory cards above it. OMD warns when the generated cards
look too short or lack explicit evidence references.

## Inspect

```bash
python -m omd.cli inspect /path/to/source.html --json
python -m omd.cli inspect "https://v.douyin.com/abc/" --with-readiness --json
```

Use `inspect` before expensive or cookie-gated sources.

## Convert

```bash
python -m omd.cli /path/to/source.html -o /path/to/vault/Inbox/note.md --quiet
python -m omd.cli report.pdf -o report.md
python -m omd.cli screenshot.png -o screenshot.md --lang chi_sim+eng
```

## Watch

```bash
python -m omd.cli watch /path/to/inbox -o /path/to/vault/Inbox
```

Use `watch` for drop-folder workflows. It converts newly stable files once.

## Batch

```bash
python -m omd.cli batch urls.txt -o /path/to/vault/Inbox
```

This is the plain conversion batch path. For Phase 1 vault capture records,
prefer `omd capture sources.txt --vault /path/to/vault --batch`. Both list
formats accept one URL, file path, or share blob per line. Blank lines and `#`
comments are ignored.

## Optional Local Polish

```bash
omd "<video-or-podcast-url>" -o transcript.md --polish
omd report.pdf -o report.md --polish-md
omd notes.md -o clean.md --polish-md
```

Polish is optional and uses local Ollama by default. `--polish-md` also accepts
an existing Markdown file directly. Using a separate output, as in the last
example, leaves `notes.md` unchanged and writes `clean.md` plus the pre-polish
copy `clean.raw.md`. Without `-o`, direct Markdown polish updates the input file
and saves `<name>.raw.md` beside it. The old `--polish-md-keep-raw` flag remains
accepted for compatibility; raw preservation is now automatic.

Review the output against the source before replacing a trusted note. Polish
is conservative formatting cleanup, not a guarantee of better wording or a
way to repair an inaccurate transcript. Fidelity checks can reject model
edits, code and protected sections are skipped, and an unavailable model keeps
the original Markdown. If a model call fails partway through, completed chunks
can remain polished while that chunk and the remaining text stay unchanged.
Large files can be refused by the safety limit; use `--force` only for an
intentional long local-model job. Keep a separate backup when repeating polish:
the raw companion records the input to the latest run.

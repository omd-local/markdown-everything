# Stage 2 packaging templates

Templates for the future `omd-app` SwiftUI Mac GUI app's release pipeline.
They live in this repo so the boring infra is already figured out before
Day-1 spike. Copy them into `omd-app` when you scaffold that repo.

```
packaging/stage2-templates/
├── release.yml          → .github/workflows/release.yml  (CI sign + notarize + DMG)
├── local-sign.sh        → scripts/local-sign.sh          (manual smoke test)
├── entitlements.plist   → packaging/entitlements.plist   (Hardened Runtime opts)
└── README.md            → docs/release.md  (or merged into the app's README)
```

---

## 1. One-time Apple Developer setup

Done once per Apple Developer account. Skip if already complete.

### 1a. Generate CSR (Certificate Signing Request)

1. Open **Keychain Access** on macOS.
2. Menu bar → **Certificate Assistant** → **Request a Certificate From a Certificate Authority...**
3. Fill in:
   - **User Email Address:** your Apple ID email
   - **Common Name:** your name (e.g. `Project Maintainer`)
   - **CA Email Address:** leave blank
   - **Request is:** Saved to disk **+** Let me specify key pair information
4. Key Size: `2048`, Algorithm: `RSA`.
5. Save as `CertificateSigningRequest.certSigningRequest`.

### 1b. Create Developer ID Application cert

1. Go to https://developer.apple.com/account → **Certificates, Identifiers & Profiles** → **Certificates** → **+**
2. Pick **Developer ID Application** (NOT "Apple Distribution" — that's App Store only).
3. Upload the `.certSigningRequest` from 1a.
4. Download `developerID_application.cer`.
5. Double-click the `.cer` to install in **login** keychain.

Verify in Keychain Access → **login** → **My Certificates**: an entry
`Developer ID Application: <Your Name> (<TEAMID>)` should appear with a
private key disclosed below it.

Note the **Team ID** (10-char alphanumeric) — find it at
https://developer.apple.com/account → Membership.

### 1c. Notarization API key (preferred over app-specific password)

1. Go to https://appstoreconnect.apple.com → **Users and Access** → **Integrations** → **Keys**
2. Section: **App Store Connect API** → **+**
3. Name: `omd-notarytool`. Access: **Developer** (NOT Admin).
4. Download `AuthKey_<KEYID>.p8` — Apple lets you download this **only once**. Store it in 1Password or your local Keychain immediately.
5. Note:
   - **Key ID** (10 chars, e.g. `ABC123XYZ4`)
   - **Issuer ID** (UUID at top of the Keys page)

### 1d. Store notarytool credentials in your local keychain

```bash
xcrun notarytool store-credentials omd-notary \
  --key ~/secrets/AuthKey_ABC123XYZ4.p8 \
  --key-id ABC123XYZ4 \
  --issuer abcd1234-5678-90ab-cdef-1234567890ab
```

`omd-notary` is just a profile name — `local-sign.sh` looks for it. Use a different name via `NOTARY_PROFILE=...` env var.

Verify:

```bash
xcrun notarytool history --keychain-profile omd-notary
# should return a (possibly empty) list, no auth errors
```

---

## 2. GitHub Actions secrets (for `omd-app` repo)

After the omd-app repo is scaffolded, set these six secrets in its
**Settings → Secrets and variables → Actions**:

| Secret | How to produce |
|---|---|
| `MACOS_CERTIFICATE_P12_BASE64` | See 2a below |
| `MACOS_CERTIFICATE_PASSWORD` | The password you set when exporting the `.p12` |
| `APPLE_TEAM_ID` | 10-char from step 1b |
| `APPLE_API_KEY_ID` | 10-char from step 1c |
| `APPLE_API_ISSUER_ID` | UUID from step 1c |
| `APPLE_API_KEY_P8_BASE64` | See 2b below |

### 2a. Export the Developer ID cert to .p12

1. **Keychain Access** → **login** → **My Certificates**
2. Right-click `Developer ID Application: <Name> (<TEAMID>)` → **Export "Developer ID Application: ..."**
3. Format: **Personal Information Exchange (.p12)**
4. Set a strong password — this becomes `MACOS_CERTIFICATE_PASSWORD`.
5. Save as `developer-id.p12`.

Base64-encode for the secret:

```bash
base64 -i developer-id.p12 | pbcopy
# paste into MACOS_CERTIFICATE_P12_BASE64 secret on GitHub
```

### 2b. Base64-encode the .p8 notarization key

```bash
base64 -i ~/secrets/AuthKey_ABC123XYZ4.p8 | pbcopy
# paste into APPLE_API_KEY_P8_BASE64 secret on GitHub
```

---

## 3. How `release.yml` is triggered

- **Tag a release** (`git tag v0.1.0 && git push --tags`) → full pipeline:
  build → sign → notarize → DMG → upload to GitHub Releases as a **draft**
  (you publish from the Releases UI after smoke-testing).
- **Workflow dispatch** (manual run from the Actions tab) → builds an
  **unsigned** `.app` and uploads it as a 7-day artifact. Useful for
  catching toolchain regressions without burning Apple's notary budget.

The job pins `BUNDLED_CLI_VERSION` to a specific `markdown-everything` tag
(currently `0.2.0`). Bump it whenever Stage 2 wants the latest CLI features.

---

## 4. How `local-sign.sh` works

Mirrors `release.yml`'s sign + notarize + staple path on your laptop.
Use it when you build the `.app` locally (no CI) and want to ship the DMG
manually, or for quick smoke tests before tagging a release.

```bash
# Assume you built omd.app via xcodebuild
./scripts/local-sign.sh /path/to/build/Release/omd.app
# → produces ~/Desktop/omd.dmg (signed + notarized + stapled)
```

Override env vars:
- `APPLE_TEAM_ID=ABC1234XYZ` — skip the keychain identity lookup
- `NOTARY_PROFILE=omd-notary-staging` — use a non-default notarytool profile
- `OUTPUT_DIR=/tmp` — write DMG somewhere other than Desktop

---

## 5. Entitlements (Hardened Runtime)

`entitlements.plist` declares the minimum set of capabilities that punch
holes in the Hardened Runtime sandbox. Every entry weakens the security
posture, so the rule is: **add only when a notarization run actually
fails because of the missing privilege**.

Currently declared:
- `com.apple.security.cs.allow-jit` — mlx_whisper JIT codegen on Apple Silicon
- `com.apple.security.cs.disable-library-validation` — PyInstaller-bundled .so files signed by Python.org, not us
- `com.apple.security.network.client` — Gumroad licence + Cloudflare telemetry + Ollama (user-configurable)

Reference: https://developer.apple.com/documentation/security/hardened_runtime

---

## 6. Smoke test before tagging v1.0.0

Before you `git push --tags v1.0.0` and let CI ship to real users:

```bash
# 1. Build locally
xcodebuild -scheme omd -configuration Release -derivedDataPath /tmp/build/

# 2. Manual sign + notarize (this script)
./scripts/local-sign.sh /tmp/build/Build/Products/Release/omd.app

# 3. Test on a fresh user account (or a Mac that's never seen this DMG)
sudo dscl . -create /Users/testuser
sudo dscl . -create /Users/testuser UserShell /bin/zsh
# ... or just use a different Mac

# 4. Confirm Gatekeeper accepts the DMG
spctl -a -t open --context context:primary-signature -v ~/Desktop/omd.dmg

# 5. Confirm app launches without warning
open ~/Desktop/omd.dmg
# should mount without "unidentified developer" prompts
```

---

## 7. Failure modes seen by similar Mac apps

(Add as Stage 2 hits them so the next dev / next-you finds the fix.)

- **Notarization fails: "The binary uses an SDK older than the 10.9 SDK."** → Xcode toolchain too old. `MACOSX_DEPLOYMENT_TARGET` should be at least 10.9.
- **Notarization fails: "The signature of the binary is invalid."** → `codesign --deep` missed something. Re-sign with `--deep --force`. Run `codesign --verify --deep --strict` to spot the offender.
- **App crashes on first launch with "killed: 9" or EXC_BAD_ACCESS in mlx** → missing `com.apple.security.cs.allow-jit`. Add to `entitlements.plist`, re-sign, re-notarize.
- **Gatekeeper still warns after notarization** → forgot to `stapler staple` the DMG. Re-run `local-sign.sh` from the staple step.
- **Notarytool hangs >30 min** → Apple notary outage. Check https://developer.apple.com/system-status/. Re-submit with `--wait --timeout 60m` on a new tag.

---

## When this README gets stale

After every Stage 2 release that hits a new failure mode worth >30 min of
debugging, append a row to section 7. Future-you (or a contractor) will
thank you.

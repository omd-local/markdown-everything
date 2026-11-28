# macOS release signing: maintainer preparation

This checklist applies to the independent **OMD Engine.app**, not to the
Obsidian plugin JavaScript. The current candidate build produces unsigned review
artifacts. Nothing in this document means signing or notarization has happened.
Home users do not need an Apple developer account, Xcode or Command Line Tools
to install the finished document app; these are maintainer release preparations.

Before using credentials, resolve the [license release blockers](third-party-licenses.md)
and pass the exact architecture's locked-dependency audit. The current Cobble
notice gap blocks public binaries; Intel additionally has unresolved cryptography
vulnerabilities without an official fixed Intel wheel. Signing cannot clear them.

## What the owner needs to prepare

| Item | Purpose | Where it belongs |
| --- | --- | --- |
| Active Apple Developer Program membership and access to the team's Account Holder | Create Developer ID certificates and accept any outstanding program agreements | Apple's developer account |
| Team ID and the intended public developer identity | Identify the signing team; verify that the identity shown to users is correct | Release configuration; the Team ID is not a password |
| **Developer ID Application** certificate **with its matching private key** | Sign the embedded executables, native libraries and app | The signing Mac's Keychain; a `.cer` file alone is insufficient |
| **Developer ID Installer** certificate with its private key, if publishing PKG | Sign the installer containing the signed app | The signing Mac's Keychain; not required for a ZIP-only distribution |
| A notarization authentication method | Authenticate `notarytool` separately from code signing | A local Keychain profile, or protected CI secrets |
| A maintained signing Mac and current supported Xcode tools | Run `codesign`, `productsign`, `notarytool`, `stapler` and verification tools | Maintainer machine or protected release runner |
| Fresh-Mac acceptance access | Test the final downloaded package, Gatekeeper and supported environments | Test Macs; retain results per architecture and minimum OS |

Use **Developer ID** certificates for distribution outside the Mac App Store.
Apple Development and Mac App Distribution certificates serve different routes.
Certificate creation requires the appropriate Apple account role; a GitHub login
does not supply Apple signing authority. Confirm the existing app identifier
`org.omd-local.engine` and package identifiers before signing an upgrade.

### Choose one notarization authentication method

- **Local release:** Apple Account email, Team ID and an app-specific password
  for the account authorized to notarize for that team. Store it with
  `xcrun notarytool store-credentials`; use its interactive password prompt rather
  than putting the password in a command, shell history or repository. Two-factor
  authentication must be configured for the Apple Account.
- **CI or API-based release:** an authorized App Store Connect API private key
  (`.p8`), its Key ID and the Issuer ID for a team key. The Issuer ID and Team ID
  are different values. Verify the key's role and the current authentication
  requirements against Apple's documentation before configuring a release job.

Neither method replaces the Developer ID certificate/private key used for code
signing. Do not paste passwords, private keys, `.p12` exports or `.p8` contents in
issues, PRs, logs or chat. If CI signing is added later, export password-protected
`.p12` identities only when needed, store them in a protected release environment,
and import them into a temporary Keychain. PR workflows remain unsigned and must
not receive release credentials. No signing secrets or signing workflow are
configured by the candidate's packaging scripts.

## Checks before credentials are used

These local checks are read-only; do not publish identity output containing
personal details unnecessarily:

```sh
xcode-select -p
xcrun notarytool --version
xcrun stapler help
security find-identity -v -p codesigning
```

Confirm the signing identity is valid and includes its private key in Keychain
Access. Check the Installer identity separately there if shipping PKG. An
installed Xcode application alone is not proof that the selected command-line
tools work. Any maintainer tool/license setup belongs on the signing machine;
it must not return to the end-user installation guide.

## Release execution and acceptance

1. Freeze the reviewed engine commit, architecture, pinned runtime/wheels and
   complete third-party license inventory. Run the document-profile build and
   record its input digests. Build arm64 and x86_64 independently.
2. Finalize all app content and metadata **before signing**. Sign nested native
   code from the inside out, then sign the app with Developer ID Application,
   secure timestamps and the hardened runtime. Determine the minimum required
   entitlements by testing the embedded Python/native modules; do not mask errors
   with blanket entitlements or use `codesign --deep` as the signing strategy.
3. Verify the signed app and repeat document Capture, isolated-Python and
   relocation smoke checks. If distributing PKG, build it around the signed app
   and sign it with Developer ID Installer. Inspect its signature and payload.
4. Submit the actual distribution container with `notarytool`, wait for an
   **Accepted** result and retain the submission ID/log. A successful upload
   alone is not acceptance. Staple and validate the app or PKG as appropriate;
   ZIP files themselves cannot carry a stapled ticket, so staple the app and
   regenerate the distributed ZIP. If both ZIP and PKG are published, validate
   both final distribution paths.
5. Generate the **final** ZIP/PKG checksums and an external file/link manifest
   after all signing and stapling changes. Keep the unsigned build manifest as
   unsigned provenance, not as the signed artifact's checksum. Do not edit
   `Contents/Resources` after signing to record the result; keep final release
   attestations outside the signed app.
6. On a separate Mac, download the exact candidate through the intended public
   route, retaining normal download quarantine. Check `codesign --verify
   --deep --strict`, Gatekeeper (`spctl --assess`) and ticket validation. Do not
   remove quarantine to turn a failed Gatekeeper test into a pass.
7. Run first-use Capture without Homebrew/Python/developer tools, app relocation,
   spaces in paths and an upgrade that preserves user data. If shipping PKG,
   actually run system Installer, including canonical app placement, CLI links
   and conflicts with existing user/Homebrew commands. CI currently builds and
   expands PKG; it does not run Installer. Record Intel, Apple Silicon, minimum
   macOS 14 and minimum Obsidian 1.11.4 coverage separately.
8. Publish only the verified final artifacts and matching hashes from the frozen
   source. Confirm the plugin's download button and main-branch guide reach the
   durable Release, not an expiring unsigned PR artifact.

Signing, notarization and real-system acceptance remain open until these steps
have evidence. Signing is not a substitute for license or vulnerability review.

## Official references

- [Signing Mac software with Developer ID](https://developer.apple.com/developer-id/)
- [Developer ID certificates](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/)
- [Notarizing macOS software before distribution](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
- [Create app-specific passwords](https://support.apple.com/en-us/102654)

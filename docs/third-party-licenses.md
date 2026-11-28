# Third-party materials in the macOS engine

Status: the missing distribution materials have been collected and pinned, but
**public binary release is still blocked by cobble 0.1.4's missing original
copyright/license notice**. A successful unsigned review build is not license
clearance. These records document provenance and known gaps; they are not a
legal certification or a vulnerability audit.

## What the app contains

`Contents/Resources/OMD-LICENSE` contains OMD's own license.
`Contents/Resources/third-party-licenses/inventory.json` records the exact
runtime, wheel hashes, original embedded notices, supplementary materials,
source URLs and unresolved release blockers. Original notices remain in the
installed packages too. Both the app ZIP and PKG carry these files inside the
same app payload; do not distribute the binary without them.

The checked-in source of the inventory is
`packaging/macos/licenses/manifest.json`. The builder is offline and uses only
the Python standard library. It rejects missing materials, altered hashes,
unreviewed wheels, unsafe material paths and a runtime update without matching
license metadata. The generated app manifest includes the license inventory
path and its unresolved blockers.

### Standalone Python

The stripped 3.12.16/20261110 archive omits the full distribution's `PYTHON.json`
and `licenses/` directory. For **each** architecture, the license snapshot comes
from the corresponding official `pgo+lto-full.tar.zst` of that same release.
The full archives were checked against GitHub's published SHA-256 digests.
Their URLs, digests, archive members and individual file hashes are recorded in
the license manifest. The runtime bytes and runtime pins have not changed.

All 20 upstream license files are retained for each architecture, together with
that architecture's `PYTHON.json`. The builder also verifies every license path
referenced by the CPython and extension metadata. This covers OpenSSL and
other linked components instead of treating Python's own license as sufficient
for the complete runtime. Some upstream license files apply to build options
not selected in this app; retaining them does not imply those features ship.

[Upstream distribution format](https://docs.astral.sh/python-build-standalone/distributions/)
explains the full archive and its license metadata. The original runtime's
bundled pip and vendored notices remain in the runtime. For the document
profile, the verified pinned pip wheel replaces that site-package installation
and its embedded notices are separately indexed with the other wheels.

### Document wheels

Every wheel in both architecture locks is covered by the inventory. Existing
license trees are copied byte for byte, including `LICENCE` spellings,
`pypdfium2`'s dependency license tree, ONNX Runtime's `ThirdPartyNotices.txt`,
and pip's vendored notices. Inventory entries link to the exact upstream PyPI
version page for source packages and project information. The third-party
packages are unmodified; OMD's launch wrappers do not replace package notices.

Four wheels lacked full license files. Supplementary materials are pinned to
immutable official source commits:

| Component | Supplementary material |
| --- | --- |
| flatbuffers 25.12.19 | Full Apache-2.0 LICENSE from release commit `7e163021e59cca4f8e1e35a7c828b5c6b7915953` |
| magika 1.0.3 | Full Apache-2.0 LICENSE from `python-v1.0.3` commit `9f225aa480e675af44343b9160f077073ed1b752` |
| markitdown 0.1.9 | Full MIT LICENSE and original copyright from release commit `21d9c1d01ecc12538dab19811fd247ef2f96ef60` |
| cobble 0.1.4 | Exact upstream README declaration, clearly labelled BSD-2-Clause reference text, and the vendored inflection MIT notice; original cobble copyright notice remains unresolved |

## Unresolved cobble release blocker

Cobble's exact wheel, source distribution and official `0.1.4` tag
(`a4f92a3462303c1402c8c55e3b5a45fa3bc2fa37`) contain no dedicated LICENSE or
original copyright notice for cobble itself. Its README identifies
BSD-2-Clause, and its package metadata identifies the author. Those statements
are retained, but we do not invent a copyright holder/year or present an
unmodified SPDX template as an original upstream notice.

Cobble also vendors `inflection.py`, which identifies Janne Vanhala's
2012–2015 MIT-licensed code. Its `underscore` function matches the AST of
inflection 0.3.1 exactly. The original MIT notice is recovered from that exact
PyPI source archive, whose digest and member path are pinned in the manifest.
This closes the vendored inflection material gap, not cobble's separate notice
gap.

Existing upstream reports [#3](https://github.com/mwilliamson/python-cobble/issues/3)
and [#4](https://github.com/mwilliamson/python-cobble/issues/4) request the missing
notice. Before public binary release, obtain the original upstream notice for
this version, or review a compatible dependency update/replacement that
supplies the required materials. Do not clear the blocker merely because a
review build or functional test succeeds.

## Enforced release check

For any release build, add `--strict-release` to the documented
`packaging/macos/build_bundle.py` command. It rejects an unresolved blocker
before publishing an output directory. With the current document profile,
this must fail with `cobble==0.1.4` until the upstream gap is resolved.
Unsigned review builds can omit the flag, but their generated inventory and
`omd-engine.json` still identify the blocker. Release automation must require
the strict check and must not promote a review artifact that reports blockers.

When updating a dependency or runtime, regenerate the inventory from the exact
hash-verified inputs, retain the original license/notice trees, record the
immutable source of supplements and review any new conditions. Then run the
packaging regressions and both native architecture builds. A manifest entry
alone does not resolve an upstream licensing ambiguity.

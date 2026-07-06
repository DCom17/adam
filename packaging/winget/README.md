# winget submission kit (MP3-P3 / roadmap P3-2)

Draft manifests for `winget install AdamLocal`. **Submit only AFTER the
installer is signed** — unsigned EXEs aren't banned by winget policy, but they
routinely trip the Defender validation pipeline into manual-review purgatory.

## When the signed v1.0 installer exists

1. Publish the release with the signed `adam-setup-v<ver>.exe` attached.
2. Fill the three `TODO` fields in `ZonteCampos.AdamLocal.installer.yaml`:
   the tagged (never `latest`) asset URL, the file's SHA256
   (`Get-FileHash dist\adam-setup-v<ver>.exe`), and the version.
3. Bump `PackageVersion` in all three files to match.
4. Fork microsoft/winget-pkgs → copy these files to
   `manifests/z/ZonteCampos/AdamLocal/<ver>/` → PR. Or let the tool do it:
   `wingetcreate submit` / `komac submit`.
5. Validate locally first — but NOTE: `winget validate` refuses a folder that
   contains this README ("does not contain a valid root"). Copy the three
   .yaml files to an empty temp folder and validate THAT:
   `winget validate --manifest <temp-folder-with-only-the-yamls>` then
   `winget install --manifest <same>` on a spare machine/VM.
   (Schema validation of the current drafts PASSED 2026-07-05 this way —
   only the three TODO fields remain.)

Notes from the July 2026 policy research (sources in docs/ROADMAP.md MP3-P3):
- Silent install/uninstall is REQUIRED and already satisfied: InstallerType
  `inno` gets /VERYSILENT by default; our running-instance guard aborts
  cleanly (exit 1) rather than hanging when Adam is open.
- The wizard's post-install downloads (Python, Claude Code) are policy-OK
  because they're disclosed in the Description (policy 1.2.3 allows declared
  dependencies).
- PrivacyUrl is expected for apps that transmit personal info (policy 1.5.1).
- First submission from a new publisher sits in a human moderation queue
  (days to ~2 weeks). PackageIdentifier naming nits are the usual friction.

This folder is NOT in the release allow-list — it never ships in the ZIP.

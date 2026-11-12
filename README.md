<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/axioweave-mono-dark.svg">
    <img src="assets/axioweave-mono.svg" width="120" alt="Axioweave — Four conditions">
  </picture>
</p>

# AxioWeave Protocol — AW-001 Research Preview

[![AW-001 protocol validation](https://github.com/haeliotang/axioweave-protocol/actions/workflows/validate.yml/badge.svg)](https://github.com/haeliotang/axioweave-protocol/actions/workflows/validate.yml)

**Status: AW-001 Stage 1 Release Candidate (`aw001-protocol-1.0.0-rc1`).**
This is a research preview of the AW-001 protocol artifacts, not a production
governance protocol and not the AxioWeave Runtime.

This repository contains the protocol/contract research only:

- AW-001 contracts, schemas and fixed action registries;
- fixtures and offline validators;
- AW-001 test suite;
- research and design documents;
- the Stage 1 snapshot manifest.

The AW-002 Rust/PostgreSQL Runtime, G1/G2 runtime and operational material are
**excluded** and remain in a private, experimental repository.

## Quick start

Requires **Python 3.11 or newer** (the validators parse ISO-8601 `Z` timestamps
with `datetime.fromisoformat`).

```sh
python3 -m pip install -r requirements-validation.txt
python3 -m unittest discover -s tests
```

The suite has **606 test methods**. It was reproduced on Python 3.14, in a
fresh virtual environment, and in a `python:3.12-slim` container; all three runs
passed. These are **local/same-host reproductions**, not an independent
third-party reproduction.

## CI

Public GitHub Actions validates both `main` and the frozen tag
`aw001-protocol-1.0.0-rc1` (606 tests + manifest/runtime-exclusion checks):

<https://github.com/haeliotang/axioweave-protocol/actions/runs/42479656418>

This is hosted validation of the **public protocol snapshot**, separate from the
private Runtime CI, which remains blocked by account billing/admission.

## What this release does and does not establish

- 606 passing tests establish only the defined AW-001 contract behavior in the
  tested scope. They do not establish real-community effectiveness, legitimacy,
  or fitness for production use.
- `SPEC_FROZEN=GRANTED` is a historical human decision for `8ba8cfe`; it does not
  automatically cover later protocol changes.
- `IMPL_CONFORMANT=GRANTED_SCOPED` was scoped to the signed baseline and reviewed
  runtime work; it is not a conformance claim for this protocol preview.
- `PILOT_AUTHORIZED=NOT_GRANTED`. No community governance authority, production
  deployment guarantee, or real pilot is granted.
- No independent third-party reproduction has been recorded yet.
- Hosted GitHub Actions was unavailable for this snapshot because of
  account-level billing/runner admission; that is not a statement that the code
  failed its tests.

## Snapshot correspondence

- Private source repository: `haeliotang/axioweave`
- Private tag: `aw001-protocol-1.0.0-rc1`
- Private tag commit: `03661d4`
- Snapshot archive SHA-256:
  `cf51c1687061613229265c0ca6a8ea31e4616f30fdaed6d6c6c0b10a6c766f08`
- 581 manifest files; per-file digests are in `MANIFEST.sha256`.

This public repository is a separate publication boundary. Its own commit and
tag record the published copy; runtime code is not included.

## Integrity

```sh
sha256sum -c MANIFEST.sha256        # GNU coreutils
shasum -a 256 -c MANIFEST.sha256    # macOS
python3 scripts/verify_snapshot.py # also checks that no runtime files are present
```

## License and rights

Apache License 2.0; see `LICENSE` and `NOTICE`.
Copyright 2026 Inceptless. The project owner confirmed that Inceptless holds the
copyright and licensing authority for the included Stage 1 files; see
`RIGHTS.md`. Contact: support@Inceptless.com.

## External review

The next milestone is `EV-001` — first independent protocol reproduction and
adversarial review. See `EV-001.md`.

See `REVIEW_GUIDE.md` for a file-by-file review map and `KNOWN_GAPS.md` for
known scope limits.

External review is explicitly invited after publication. Please record the
snapshot SHA-256, OS/Python versions, the exact test command, and any protocol
ambiguities or contradictory fixtures. Formal 1.0.0 publication will require an
independent reproduction record.

## Citation

Use `CITATION.cff` or cite the frozen release:

```text
AxioWeave AW-001 Protocol Research Preview, 1.0.0-rc1.
https://github.com/haeliotang/axioweave-protocol/releases/tag/aw001-protocol-1.0.0-rc1
```

<p align="center">
  <img src="https://raw.githubusercontent.com/haeliotang/axioweave-protocol/main/assets/axioweave-mono.svg" width="120" alt="Axioweave — Four conditions">
</p>

# AW-001 Protocol Research Preview — `aw001-protocol-1.0.0-rc1`

**Release type:** research candidate / public preview. Not a production
governance protocol and not a Runtime release.

## Included

- AW-001 contracts, schemas, fixed action registries, fixtures and validators.
- AW-001 test suite (606 methods) and research documents.
- Stage 1 snapshot manifest and Apache-2.0 `LICENSE` / `NOTICE`.

## Excluded

- AW-002 Rust/PostgreSQL Runtime, G1/G2 runtime, CI workflows, SBOM and
  operational material. Runtime remains private and experimental.

## Evidence

- Snapshot from private tag `aw001-protocol-1.0.0-rc1`, commit `03661d4`.
- Archive SHA-256:
  `cf51c1687061613229265c0ca6a8ea31e4616f30fdaed6d6c6c0b10a6c766f08`.
- 606/606 tests passed on extracted snapshot:
  - Python 3.14 same host;
  - fresh virtual environment;
  - `python:3.12-slim` container.
- These are locally reproduced; **no independent third-party reproduction is
  claimed** for this RC.

## Required disclosures

- 606 tests cover only the defined contract behavior in their tested scope; they
  do not demonstrate real-world governance effectiveness.
- `SPEC_FROZEN=GRANTED` applies to historical baseline `8ba8cfe` and does not
  automatically cover later protocol changes.
- `PILOT_AUTHORIZED=NOT_GRANTED`. No production deployment guarantee, community
  mandate, or pilot authorization is granted.
- Hosted GitHub Actions was blocked by account-level billing/runner admission for
  the latest private snapshot. This is disclosed as a CI availability fact, not
  as a test failure or quality conclusion.
- Apache-2.0 applies to the files in this public snapshot only. The private
  Runtime and other repository material are not published or relicensed by this
  release.

## Runtime requirement

Python 3.11 or newer is required by the validators. CI validates 3.11 and 3.12;
Python 3.10 is not supported.

## CI evidence

Public GitHub Actions run (green on `main` and the frozen RC tag):

<https://github.com/haeliotang/axioweave-protocol/actions/runs/42479656418>

This is hosted validation of the public protocol snapshot, not the private
Runtime CI.

## Rights

Copyright 2026 Inceptless. The project owner confirmed Inceptless holds the
copyright/licensing authority for the included files; see `RIGHTS.md` and
`NOTICE`.

## Review request

Independent researchers are invited to run the suite, inspect contract
self-consistency, compare alternate implementations, and report where the
protocol cannot express real multi-party value conflict. A formal 1.0.0 release
will require an external reproduction record.

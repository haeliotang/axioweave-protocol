# AxioWeave Stage 1 — AW-001 Protocol Release Candidate

Status: **RELEASE_CANDIDATE**, not a final release and not a runtime
conformance claim.

This snapshot contains the AW-001 protocol research artifacts: schemas, fixed
action registries, fixtures, offline validators, AW-001 tests, research/design
documents, and the Apache-2.0 license/NOTICE. The AW-002 Rust runtime,
PostgreSQL boundary, G1/G2 runtime, CI workflows and operational material are
**excluded** and remain experimental/private.

## Quick start

```sh
python3 -m pip install -r requirements-validation.txt
python3 -m unittest discover -s tests -v
```

The suite covers AW-001 Slices 1, 2A/2B/2C, 3A/3B/3C and 4A/4B/4C. A successful
run is expected to discover **606 test methods**. This snapshot does not run
PostgreSQL, Cargo, a browser, or an AI provider.

## Gate and evidence status

- `SPEC_FROZEN=GRANTED` was signed for the historical baseline `8ba8cfe`; it
  does not automatically cover later protocol changes.
- `IMPL_CONFORMANT=GRANTED_SCOPED` was scoped to `8ba8cfe` and the runtime work
  then reviewed; it is **not** a claim about this snapshot or a production
  runtime.
- `PILOT_AUTHORIZED=NOT_GRANTED`.
- Hosted CI for this exact snapshot may be unavailable because of GitHub Actions
  account/billing admission. Independent local reproduction is required and
  should be recorded by the reviewer.

## Known limitations

- Stage 1 is a protocol/reference candidate, not a runtime conformance claim.
- Full AW-001 validator/envelope semantics are not claimed complete.
- No real IdP, AI model provider, 24-hour soak, or pilot authorization is
  included.
- Ballots/eligibility import policy is not decided here.
- Apache-2.0 is a license grant for the files in this snapshot, not a statement
  that the whole AxioWeave repository has been published.

## License and ownership

Apache License 2.0; see `LICENSE`.
Copyright 2026 Inceptless. See `NOTICE`. Contact: support@Inceptless.com.

## Snapshot integrity

See `MANIFEST.sha256` for per-file SHA-256 digests, and
`specs/aw001-stage1-release/stage1-release-candidate.json` in the source
repository for the fixed source commit, archive digest, scope and limitations.

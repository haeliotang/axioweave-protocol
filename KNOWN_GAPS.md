# Known Gaps (Stage 1 RC)

This list is intentionally public. It is not a claim that the listed items are
the only gaps.

- **Historical freeze scope:** `SPEC_FROZEN=GRANTED` applies to baseline
  `8ba8cfe`, not automatically to changes made after that signature.
- **Independent reproduction:** no non-maintainer reproduction is recorded yet.
- **Real-community validation:** no partner, pilot, public deliberation or
  real-world governance authorization exists. `PILOT_AUTHORIZED=NOT_GRANTED`.
- **Runtime excluded:** AW-002 Rust/PostgreSQL runtime, G1/G2 runtime and
  operational material are not in this repository and are not covered by this
  preview.
- **Full validator/envelope semantics:** the complete AW-001 validator layer is
  not claimed complete; some checks are partial or fixture-scoped.
- **Ballots/eligibility import policy:** not decided or published here.
- **Production conformance:** not granted; no deployment, availability,
  security-certification, or data-protection guarantee.
- **External AI/IdP:** no real model provider or identity provider is part of
  this protocol preview.
- **Value conflict expressiveness:** the suite does not prove that the protocol
  can preserve every form of dissent under real power asymmetry.

- **Python version:** the suite requires Python 3.11+; on Python 3.10 the
  `datetime.fromisoformat("...Z")` calls fail. The CI matrix intentionally
  validates 3.11 and 3.12, not 3.10. This is a compatibility limit, not a
  protocol semantic change.

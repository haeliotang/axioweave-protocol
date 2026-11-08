# Review Guide

This guide helps an external reviewer find the parts of the AW-001 research
preview that are most worth challenging. It does not replace the schemas,
fixtures or tests; those remain authoritative.

## What to run first

```sh
python3 -m pip install -r requirements-validation.txt
python3 scripts/verify_snapshot.py
python3 -m unittest discover -s tests -v
sha256sum -c MANIFEST.sha256   # shasum -a 256 -c on macOS
```

Expected: 606 passing tests and 581 manifest files.

## Review map

| Area | Start here | What to challenge |
| --- | --- | --- |
| Core transition envelope | `specs/aw001_slice1/`, `tools/validate_aw001_slice1.py` | required/rejected fields, four-tuple completeness, error layer selection |
| Protective orders | `specs/aw001_slice2/2a/` | issue/lift/expire authority, revocation and effective-time boundaries |
| Commitments and tickets | `specs/aw001_slice2/2b/` | partial fulfillment, verification, handoff/cancel transitions |
| Appeals, collective approval, recusal | `specs/aw001_slice2/2c/` | quorum, recusal sets, deferral, who may adjudicate |
| Inclusion/participation audit | `specs/aw001_slice3/3a/` | participant audit, speaker statements, projection consistency |
| UTC buckets/uncertainty | `specs/aw001_slice3/3b/` | directional uncertainty, bucket boundaries, ordering |
| Rounds and ballots | `specs/aw001_slice3/3c/` | secret-ballot isolation, audit separation, close/tally |
| Markings and lineage | `specs/aw001_slice4/4a/` | marking monotonicity, derived markings, lineage scope |
| Branches and AI drafts | `specs/aw001_slice4/4b/` | fixed baseline, draft boundaries, confirmation |
| Output/recovery/templates | `specs/aw001_slice4/4c/` | withdrawal/recovery, template installation, deployment exchange |

## Cross-cutting questions

1. **Authority:** is every state change traceable to an explicit permission, or
   can a role/actor be inferred implicitly?
2. **Dissent:** is minority/persistent dissent preserved, or collapsed when a
   decision is recorded?
3. **Power asymmetry:** can a stronger party force a transition by controlling
   evidence, timing or adjudication? Where would the protocol detect it?
4. **Recusal and remedy:** are recusal, appeal and remedy first-class states, or
   only comments?
5. **Ballot privacy:** can any fixture or projection link a ballot to a member?
6. **Failure:** what happens on rejected, deferred, timed-out or recovered
   transitions? Is the failure state itself auditable?

## Designing a new conflict case

Do not rewrite an existing fixture. Write a case where:

- the parties have unequal authority or resources;
- both sides rely on evidence the other disputes;
- at least one dissent persists after the decision;
- a decision-maker has a conflict and must recuse;
- an appeal and a remedy are attempted.

Then mark, for each step, whether the current contracts can express it. A
negative answer is a useful review result.

## Known scope limits

See `KNOWN_GAPS.md`. The 606 tests are evidence of defined contract behavior,
not of real-community legitimacy or production readiness.

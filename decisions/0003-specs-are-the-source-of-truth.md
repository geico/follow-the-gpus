# 0003. Specs are the source of truth

- Status: accepted
- Date: 2026-10-11

## Context

The goal: a person can build the product again from `spec/` only. The product is the GPU capacity tracker. No rule tied a change to the spec. The review checked only the stated goal of the change. Thus the spec can drift from the product.

A spec that changes with each pull request can still have gaps. The goal needs a direct test.

## Decision

1. `spec/` is the source of truth for the product.
2. A change to product behavior must change the spec in the same pull request.
3. The spec must state each requirement. A link to `research/` gives only the reason. A reader of `spec/` must not need `research/` to build the product.
4. The spec marks each item that is not decided:
   - "Open": decide it before the build.
   - "Implementer's choice": any answer conforms to the spec.
5. The spec completeness check measures the goal. A reader with a fresh context reads only `spec/`. It lists each point where it must guess. Each guess is a gap in the spec.
6. A review runs the spec completeness check on each changed spec section. Each guess is a required fix.
7. Scope: the product only. The repo tooling keeps its description in `CONTRIBUTING.md`, and the ADRs. A change to tooling behavior must update one of them in the same pull request. A tooling file is its own description.

## Consequences

- each change states its spec change, or the reason for no change.
- Requirements and decisions are not factual claims, so they need no source link. A spec change that adds factual claims gives a source link for each claim. A review and `make lint test` check the change before the merge.
- No product exists yet. A full spec completeness check of `spec/` runs when the product work starts.

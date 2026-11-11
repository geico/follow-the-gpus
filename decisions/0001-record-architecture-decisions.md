# 0001. Record architecture decisions

- Status: accepted
- Date: 2026-10-11

## Context

People and agents work on this repo in parallel. They need the reasons for earlier choices. Chat history and agent memory are not shared between sessions or machines.

## Decision

Each decision is 1 Markdown file in `decisions/`, named `NNNN-<slug>.md`. Each file gives the status, the date, the context, the decision, and the consequences.

## Consequences

- A new decision takes the next free number.
- A changed decision gets a new ADR. The old ADR gets the status "superseded by NNNN".

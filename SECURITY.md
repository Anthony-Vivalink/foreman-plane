# Security Policy

## Supported versions

foreman-plane is a single-developer personal tool. Fixes land on `main` and
are released as a patch version; there is no long-term-support branch.

| Version | Supported |
| --- | --- |
| 0.2.x | yes |
| below 0.2 | no |

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Preferred channel — GitHub private advisory. It opens a private thread between
you and the maintainer:

<https://github.com/Anthony-Vivalink/foreman-plane/security/advisories/new>

> If that page shows no "Report a vulnerability" form, private reporting is
> not enabled on this repository yet. In that case open a public issue that
> contains **only** a one-line summary (no proof of concept, no exploit
> details) and ask for a private channel there.

Please include, as far as you can:

* the affected version and the entry point (CLI, MCP server, or a task card);
* what an attacker actually gains;
* a minimal reproduction, if it is safe to share;
* whether the issue is already public somewhere else.

## What to expect

* An acknowledgement within a few days. This is a volunteer project, so
  please allow about a week before following up.
* Either a fix plus a patch release, or an explanation of why the report is
  considered out of scope.

## Scope

In scope: the CLI, the MCP stdio server, the ledger and the acceptance
evaluation — that is, anything under `src/foreman_plane/`.

Out of scope: the execution bodies themselves (they are separate programs that
foreman-plane merely invokes) and anything wrong with a user's own
`agents.json` or task cards.

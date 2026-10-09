# Routing QUESTIONS fixture — fleet rule D-825 GO-ref tests

Fake artefact tree, read only through `AUTOOS_ROUTING_DIR`. The ONLY `OS-<n>` items in
this file are OS-0, OS-1 and OS-2 — naming an absent id here would defeat the test.

## OS-0 — may a live gateway apply run without a judge GO?

No. Ask first; silence is HOLD (D-825).

## OS-1 — where does the gate check the reference?

Before any docker, token or gateway call, so a refused run touches nothing.

## OS-2 — does the gate accept an id it cannot find?

No — an unreadable or missing source refuses too, unless the operator passes the
offline flag, which is logged.

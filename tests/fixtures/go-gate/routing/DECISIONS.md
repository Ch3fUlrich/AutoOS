# Consolidated decisions fixture — fleet rule D-825

Second accepted source for a `D-<n>` ref (the gate reads the decisions log AND this
file). The ONLY decision id in this file is the one below — naming an absent id here
would defeat the exact-id test.

## 5.7 Live and infra steps need a GO (D-825)

A live or infra step never proceeds on silence; the tools that apply take a `--go`
reference naming the judge artefact and refuse without it.

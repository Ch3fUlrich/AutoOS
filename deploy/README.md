# deploy/

Host-unit templates (systemd `.service` and `.timer` units) and hook templates.
They ship as **templates**: placeholders only — no hostnames, no usernames, no
paths under `/home`, no ports, no addresses. Real values are rendered at install
time from the gitignored inventory, on the machine being provisioned.

- Copy a template, render it for the target, keep the rendered unit out of git.
- This whole tree is published and is scanned by CI's `Public scrub scan`
  (`scripts/public-scrub/scan.py`); a hit there fails the branch.

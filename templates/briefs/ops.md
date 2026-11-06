# {{task_id}} — {{one_line}}
FILES (only these; <= 3 files, <= 200 lines changed): {{paths}}. Do not touch other lines/files.
GOAL: {{goal}}.
INVARIANTS (must hold on every path): {{invariants}}
SECRETS: read keys only from {{keys_file}} (0600, READ-ONLY, do not edit) like {{reference_playbook}} (READ-ONLY, do not edit); never via environment:, script args, argv, logs or env vars.
ALERTS/MAIL: at most once per {{entity}}; persist alerted state in {{state_path}}; test proves no repeat.
EDIT METHOD: anchored edits; never rewrite a whole YAML file; keep indentation width {{indent}}; trailing newline.
DONE = all of; CHECK n reports DONE item n (do not paste this numbered list into REPORT):
  1 yamllint {{files}} -> 0 errors
  2 ansible-playbook --syntax-check {{playbooks}} -> ok
  3 ansible-lint {{playbooks}} -> 0 errors (warnings listed)
  4 gitleaks/secret-scan on diff -> clean
  5 tests: {{test_files}} (include NEW ones) -> all pass (no subsets)
  6 git diff --stat within FILES
Missing tool = STOP (input_required); use the uppercase INPUT_REQUIRED verdict on the CHECK line, never skip a check.
REPORT: {expected_output, checks[1-6] with output tail, confidence, open questions}
Put each check on its own line at column 0, using `CHECK n: PASS <evidence>`, `CHECK n: FAIL <evidence>` or `CHECK n: INPUT_REQUIRED <evidence>`, with no list marker, bold or indentation; replace n with the check number and <evidence> with the real output.
The CHECK line itself must carry non-empty evidence; give each check one verdict; a second line with a different verdict fails. A CHECK line inside a fenced block opened with at most 3 leading spaces, or a line that itself starts with `>`, is never counted.
Put the output tail in a fenced block under that line; the fenced tail does not count as that evidence. A bare verdict counts as FAIL; do not append a corrected duplicate CHECK line.

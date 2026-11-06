# {{task_id}} — {{one_line}}
FILES (only these; <= 3 files, <= 200 lines changed): {{paths}}. Do not touch other lines/files.
GOAL: {{goal}}.
INVARIANTS (must hold on every path): {{invariants}}
SECRETS: read keys only from {{keys_file}} (0600, READ-ONLY, do not edit) like {{reference_playbook}} (READ-ONLY, do not edit); never via environment:, script args, argv, logs or env vars.
ALERTS/MAIL: at most once per {{entity}}; persist alerted state in {{state_path}}; test proves no repeat.
EDIT METHOD: anchored edits; never rewrite a whole YAML file; keep indentation width {{indent}}; trailing newline.
DONE = all of, pasted into REPORT:
  1 yamllint {{files}} -> 0 errors
  2 ansible-playbook --syntax-check {{playbooks}} -> ok
  3 ansible-lint {{playbooks}} -> 0 errors (warnings listed)
  4 gitleaks/secret-scan on diff -> clean
  5 tests: {{test_files}} (include NEW ones) -> all pass (no subsets)
  6 git diff --stat within FILES
Missing tool = STOP and report input_required, never skip a check.
REPORT: {expected_output, checks[1-6] with output tail, confidence, open questions}
Report each check on its own line at column 0, literally `CHECK n: PASS <evidence>`, `CHECK n: FAIL <evidence>` or `CHECK n: INPUT_REQUIRED <evidence>`, with no list marker, bold or indentation.
Put the output tail in a fenced block under that line; a bare verdict with no evidence does not count.

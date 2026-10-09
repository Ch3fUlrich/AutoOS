# {{task_id}} — {{one_line}}
FILES (only these; <= 3 files, <= 200 lines changed): {{paths}}. Do not touch other lines/files.
GOAL: {{goal}}.
INVARIANTS (must hold on every path): {{invariants}}
SECRETS: read keys only from {{keys_file}} (0600) like {{reference_playbook}}; never via environment:, script args, argv, logs or env vars.
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

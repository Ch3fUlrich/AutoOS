#!/usr/bin/env python3
"""FLEET-HOOKS (P0): tests/hooks/bash_guard.py, the PreToolUse guard for the
Bash tool.

Every case runs the hook as a real subprocess with the hook's own PreToolUse
JSON on stdin - exactly the shape and transport Claude Code uses - and reads
back the exit code and stderr text. Nothing here imports the hook module
directly: a hook that behaves correctly when called but not when run is not
proven at all.

CASES is one table: each row is a shell command plus whether the guard must
deny it and, when it denies, a substring its stderr message must carry. The
rows cover the two deny shapes (an unquoted heredoc whose body carries a
backtick/`$(`, and a `claude --bg`/`-p`/`--print` call whose double-quoted
argument carries one) and every false-positive guard named in the brief:
quoted heredoc delimiters (`'EOF'`, `"EOF"`, `\\EOF`), a literal `<<` inside
a double-quoted string, the `$((...))` arithmetic shift operator, a
single-quoted or escaped backtick, a bare `$VAR`, an unrelated flag, and text
after a heredoc's terminator line.

Run directly:

    python3 tests/test_bash_guard.py
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "tools" / "hooks" / "bash_guard.py"


def run_hook_raw(stdin_text):
    return subprocess.run([sys.executable, str(HOOK)], input=stdin_text,
                           capture_output=True, text=True)


def run_hook_bytes(stdin_bytes):
    return subprocess.run([sys.executable, str(HOOK)], input=stdin_bytes,
                           capture_output=True)


def run_hook(payload):
    return run_hook_raw(json.dumps(payload))


def run_bash(command):
    return run_hook({"tool_name": "Bash", "tool_input": {"command": command}})


# (name, command, expect_deny, expect_substring_in_stderr_or_None)
CASES = [
    # ─── heredoc: the incident shape and its variants (DENY) ──────────
    ("incident_shape_unquoted_heredoc_backtick_prose",
     "cat <<EOF\nIf this fails, run:\n`netplan apply`\nEOF\n",
     True, "heredoc"),
    ("dash_heredoc_tab_terminator_with_command_substitution",
     "cat <<-EOF\n\tsome text $(whoami)\n\tEOF\n",
     True, "heredoc"),
    ("unquoted_heredoc_dollar_paren_no_backtick",
     "cat <<EOF\nrun $(id)\nEOF\n",
     True, "heredoc"),
    ("delimiter_reuse_first_clean_second_dirty",
     "cat <<EOF\nclean body\nEOF\ncat <<EOF\ndirty `id`\nEOF\n",
     True, "heredoc"),
    ("delimiter_reuse_first_dirty_second_clean",
     "cat <<EOF\ndirty `id`\nEOF\ncat <<EOF\nclean body\nEOF\n",
     True, "heredoc"),
    ("heredoc_inside_command_substitution",
     "x=$(cat <<EOF\nhello `id`\nEOF\n)\n",
     True, "heredoc"),
    ("heredoc_before_pipe_with_dirty_body",
     "cat <<EOF | grep x\nbad `id`\nEOF\n",
     True, "heredoc"),
    ("custom_delimiter_name_dirty",
     "cat <<PROMPT\ntext `id`\nPROMPT\n",
     True, "heredoc"),

    # ─── heredoc false-positive guards (ALLOW) ─────────────────────────
    ("single_quoted_delimiter_allows_backtick_body",
     "cat <<'EOF'\n`id`\nEOF\n",
     False, None),
    ("double_quoted_delimiter_allows_backtick_body",
     'cat <<"EOF"\n`id`\nEOF\n',
     False, None),
    ("backslash_escaped_delimiter_allows_backtick_body",
     "cat <<\\EOF\n`id`\nEOF\n",
     False, None),
    ("literal_angle_brackets_in_double_quoted_string",
     'echo "a << b"',
     False, None),
    ("arithmetic_left_shift_not_heredoc",
     "echo $((1<<3))",
     False, None),
    ("clean_heredoc_body_allows",
     "cat <<EOF\nnothing dangerous here\nEOF\n",
     False, None),
    ("text_after_terminator_is_not_body",
     "cat <<EOF\nfoo\nEOF\necho 'bar `cmd`'\n",
     False, None),
    ("comment_hides_heredoc_operator",
     "echo hi # cat <<EOF",
     False, None),

    # ─── claude --bg / -p / --print (DENY) ─────────────────────────────
    ("claude_bg_backtick_prompt",
     'claude --bg "do `whoami`"',
     True, "claude"),
    ("claude_dash_p_dollar_paren_prompt",
     'claude -p "run $(id)"',
     True, "claude"),
    ("claude_print_long_flag_backtick_prompt",
     'claude --print "text `ls`"',
     True, "claude"),
    ("claude_prompt_before_bg_flag",
     'claude "cmd `id`" --bg',
     True, "claude"),
    ("claude_other_flags_interspersed",
     'claude --model x --bg --verbose "report $(pwd)"',
     True, "claude"),
    ("claude_absolute_path_invocation",
     '/usr/local/bin/claude --bg "r `id`"',
     True, "claude"),
    ("claude_env_assignment_prefix",
     'FOO=bar claude --bg "x `id`"',
     True, "claude"),
    ("claude_after_and_and",
     'echo hi && claude --bg "x `id`"',
     True, "claude"),
    ("claude_after_pipe",
     'echo hi | claude -p "x `id`"',
     True, "claude"),

    # ─── claude false-positive guards (ALLOW) ──────────────────────────
    ("claude_bg_plain_text_allows",
     'claude --bg "plain text"',
     False, None),
    ("claude_bg_single_quoted_backtick_allows",
     "claude --bg 'has a backtick `here`'",
     False, None),
    ("claude_bg_escaped_backtick_allows",
     r'claude --bg "uses \`escaped\` backtick"',
     False, None),
    ("claude_bg_plain_dollar_var_allows",
     'claude --bg "uses $HOME only"',
     False, None),
    ("claude_no_bg_or_print_flag_allows",
     'claude "no bg or print flag `here`"',
     False, None),
    ("claude_unrelated_flag_allows",
     'claude --verbose "no special flag `here`"',
     False, None),
    # over-deny: harmless
    ("claude_dash_prefix_dirty_denied",
     'claude -prefix "not the -p flag `here`"',
     True, "claude"),
    # over-deny: harmless
    ("claude_dash_print_dirty_denied",
     'claude -print "not the -p flag `here`"',
     True, "claude"),
    # over-deny: harmless
    ("claude_dash_pretty_dirty_denied",
     'claude -pretty "not the -p flag `here`"',
     True, "claude"),
    ("non_claude_command_with_bg_flag_allows",
     'somecmd --bg "x `id`"',
     False, None),

    # ─── combined ───────────────────────────────────────────────────
    ("heredoc_and_claude_both_clean_allows",
     'cat <<EOF\nfine\nEOF\nclaude --bg "ok"\n',
     False, None),

    # ─── round 2: wrappers (item 1) ───────────────────────────────────
    ("sudo_claude_bg_denied",
     'sudo claude --bg "do $(whoami)"',
     True, "claude"),
    ("sudo_claude_bg_allows",
     'sudo claude --bg "plain text"',
     False, None),
    ("sudo_u_root_claude_p_denied",
     'sudo -u root claude -p "run $(id)"',
     True, "claude"),
    ("sudo_u_root_claude_p_allows",
     'sudo -u root claude -p "plain text"',
     False, None),
    ("env_var_claude_p_denied",
     'env VAR=1 claude -p "run $(id)"',
     True, "claude"),
    ("env_var_claude_p_allows",
     'env VAR=1 claude -p "plain text"',
     False, None),
    ("env_i_claude_print_denied",
     'env -i claude --print "text $(whoami)"',
     True, "claude"),
    ("env_i_claude_print_allows",
     'env -i claude --print "plain text"',
     False, None),
    ("nohup_claude_bg_denied",
     'nohup claude --bg "run $(id)"',
     True, "claude"),
    ("nohup_claude_bg_allows",
     'nohup claude --bg "plain text"',
     False, None),
    ("time_claude_p_denied",
     'time claude -p "run $(id)"',
     True, "claude"),
    ("time_claude_p_allows",
     'time claude -p "plain text"',
     False, None),
    ("exec_claude_bg_denied",
     'exec claude --bg "run $(id)"',
     True, "claude"),
    ("exec_claude_bg_allows",
     'exec claude --bg "plain text"',
     False, None),
    ("timeout_claude_p_denied",
     'timeout 5 claude -p "run $(id)"',
     True, "claude"),
    ("timeout_claude_p_allows",
     'timeout 5 claude -p "plain text"',
     False, None),
    ("nice_claude_print_denied",
     'nice -n 5 claude --print "text $(id)"',
     True, "claude"),
    ("nice_claude_print_allows",
     'nice -n 5 claude --print "plain text"',
     False, None),
    ("wrapper_non_claude_backtick_allows",
     'sudo ls "`id`"',
     False, None),
    ("env_u_claude_p_denied",
     'env -u HOME claude -p "$(id)"',
     True, "claude"),
    ("env_u_claude_p_allows",
     'env -u HOME claude -p "plain text"',
     False, None),
    ("command_claude_p_denied",
     'command claude -p "$(id)"',
     True, "claude"),
    ("command_claude_p_allows",
     'command claude -p "plain text"',
     False, None),
    ("builtin_claude_p_denied",
     'builtin claude -p "$(id)"',
     True, "claude"),
    ("builtin_claude_p_allows",
     'builtin claude -p "plain text"',
     False, None),
    ("setsid_claude_p_denied",
     'setsid claude -p "$(id)"',
     True, "claude"),
    ("setsid_claude_p_allows",
     'setsid claude -p "plain text"',
     False, None),
    ("sudo_terminator_claude_p_denied",
     'sudo -- claude -p "$(id)"',
     True, "claude"),
    ("sudo_terminator_claude_p_allows",
     'sudo -- claude -p "plain text"',
     False, None),
    ("sudo_u_root_terminator_claude_p_denied",
     'sudo -u root -- claude -p "$(id)"',
     True, "claude"),
    ("sudo_u_root_terminator_claude_p_allows",
     'sudo -u root -- claude -p "plain text"',
     False, None),

    # ─── round 2: flag with = (item 2) ────────────────────────────────
    ("claude_print_equals_denied",
     'claude --print="$(id)"',
     True, "claude"),
    ("claude_print_equals_allows",
     'claude --print="plain"',
     False, None),

    # ─── round 2: clustered short flags (item 3) ─────────────────────
    ("claude_clustered_short_flags_denied",
     'claude -pq "text `id`"',
     True, "claude"),
    ("claude_clustered_short_flags_allows",
     'claude -pq "plain text"',
     False, None),
    ("claude_cluster_pq_denied",
     'claude -pq "backtick `id`"',
     True, "claude"),
    ("claude_cluster_cp_denied",
     'claude -cp "backtick `id`"',
     True, "claude"),
    ("claude_cluster_cp_allows",
     'claude -cp "plain text"',
     False, None),
    ("claude_cluster_pc_denied",
     'claude -pc "backtick `id`"',
     True, "claude"),
    ("claude_cluster_pc_allows",
     'claude -pc "plain text"',
     False, None),

    # ─── round 2: heredoc body escapes (item 4) ──────────────────────
    ("heredoc_escaped_backtick_allows",
     'cat <<EOF\n\\`id\\`\nEOF\n',
     False, None),
    ("heredoc_escaped_dollar_paren_allows",
     'cat <<EOF\nuse \\$(whoami) literally\nEOF\n',
     False, None),
    ("heredoc_escaped_backslash_live_backtick_denied",
     'cat <<EOF\n\\\\`id`\nEOF\n',
     True, "heredoc"),

    # ─── round 2: recursive shell -c scan (item 5) ───────────────────
    ("bash_c_claude_bg_denied",
     'bash -c \'claude --bg "$(whoami)"\'',
     True, "claude"),
    ("sh_c_claude_p_denied",
     'sh -c \'claude -p "`id`"\'',
     True, "claude"),
    ("zsh_c_claude_bg_denied",
     'zsh -c \'claude --bg "$(whoami)"\'',
     True, "claude"),
    ("dash_c_claude_p_denied",
     'dash -c \'claude -p "`id`"\'',
     True, "claude"),
    ("nested_bash_c_claude_print_denied",
     'bash -c "bash -c \'claude --print=\\"$(id)\\"\'"',
     True, "claude"),
    ("sudo_bash_c_inner_escaped_denied",
     'sudo bash -c "claude -p \\"`id`\\""',
     True, "claude"),
    ("bash_c_ls_dollar_paren_allows",
     'bash -c \'ls "$(pwd)"\'',
     False, None),
    ("bash_c_echo_plain_allows",
     'bash -c \'echo plain\'',
     False, None),
    ("bash_lc_claude_p_denied",
     'bash -lc \'claude -p "$(id)"\'',
     True, "claude"),
    ("bash_lc_claude_p_allows",
     'bash -lc \'claude -p "plain text"\'',
     False, None),
    ("bash_ec_claude_p_denied",
     'bash -ec \'claude -p "$(id)"\'',
     True, "claude"),
    ("bash_ec_claude_p_allows",
     'bash -ec \'claude -p "plain text"\'',
     False, None),
    ("bin_bash_c_claude_p_denied",
     '/bin/bash -c \'claude -p "$(id)"\'',
     True, "claude"),
    ("bin_bash_c_claude_p_allows",
     '/bin/bash -c \'claude -p "plain text"\'',
     False, None),
    ("usr_bin_env_bash_c_claude_p_denied",
     '/usr/bin/env bash -c \'claude -p "$(id)"\'',
     True, "claude"),
    ("usr_bin_env_bash_c_claude_p_allows",
     '/usr/bin/env bash -c \'claude -p "plain text"\'',
     False, None),

    # ─── round 2.2: clean prompt allows for -prefix, -print, -pretty ──
    ("claude_dash_prefix_clean_allows",
     'claude -prefix "plain text"',
     False, None),
    ("claude_dash_print_clean_allows",
     'claude -print "plain text"',
     False, None),
    ("claude_dash_pretty_clean_allows",
     'claude -pretty "plain text"',
     False, None),

    # ─── round 2.2: short p clusters with dirty prompt (DENY) ────────
    ("claude_cluster_pz_dirty_denied",
     'claude -pz "$(id)"',
     True, "claude"),
    ("claude_cluster_pn_dirty_denied",
     'claude -pn "$(id)"',
     True, "claude"),
    ("claude_cluster_pw_dirty_denied",
     'claude -pw "$(id)"',
     True, "claude"),
    ("claude_cluster_pe_dirty_denied",
     'claude -pe "$(id)"',
     True, "claude"),
    ("claude_cluster_pm_dirty_denied",
     'claude -pm "$(id)"',
     True, "claude"),
    ("claude_cluster_px_dirty_denied",
     'claude -px "$(id)"',
     True, "claude"),
    ("claude_cluster_np_dirty_denied",
     'claude -np "$(id)"',
     True, "claude"),
    ("claude_cluster_wp_dirty_denied",
     'claude -wp "$(id)"',
     True, "claude"),
    ("claude_cluster_dp_dirty_denied",
     'claude -dp "$(id)"',
     True, "claude"),
    ("claude_cluster_hp_dirty_denied",
     'claude -hp "$(id)"',
     True, "claude"),
    ("claude_cluster_ip_dirty_denied",
     'claude -ip "$(id)"',
     True, "claude"),
    ("claude_cluster_rp_dirty_denied",
     'claude -rp "$(id)"',
     True, "claude"),
    ("claude_cluster_vp_dirty_denied",
     'claude -vp "$(id)"',
     True, "claude"),
    ("claude_cluster_cp_dirty_denied",
     'claude -cp "$(id)"',
     True, "claude"),
    ("claude_cluster_pc_dirty_denied",
     'claude -pc "$(id)"',
     True, "claude"),
    ("claude_cluster_pq_dirty_denied",
     'claude -pq "$(id)"',
     True, "claude"),

    # ─── round 2.2: short p clusters with clean prompt (ALLOW) ───────
    ("claude_cluster_pz_clean_allows",
     'claude -pz "plain text"',
     False, None),
    ("claude_cluster_pn_clean_allows",
     'claude -pn "plain text"',
     False, None),
    ("claude_cluster_pw_clean_allows",
     'claude -pw "plain text"',
     False, None),
    ("claude_cluster_pe_clean_allows",
     'claude -pe "plain text"',
     False, None),
    ("claude_cluster_pm_clean_allows",
     'claude -pm "plain text"',
     False, None),
    ("claude_cluster_px_clean_allows",
     'claude -px "plain text"',
     False, None),
    ("claude_cluster_np_clean_allows",
     'claude -np "plain text"',
     False, None),
    ("claude_cluster_wp_clean_allows",
     'claude -wp "plain text"',
     False, None),
    ("claude_cluster_dp_clean_allows",
     'claude -dp "plain text"',
     False, None),
    ("claude_cluster_hp_clean_allows",
     'claude -hp "plain text"',
     False, None),
    ("claude_cluster_ip_clean_allows",
     'claude -ip "plain text"',
     False, None),
    ("claude_cluster_rp_clean_allows",
     'claude -rp "plain text"',
     False, None),
    ("claude_cluster_vp_clean_allows",
     'claude -vp "plain text"',
     False, None),
    ("claude_cluster_cp_clean_allows",
     'claude -cp "plain text"',
     False, None),
    ("claude_cluster_pc_clean_allows",
     'claude -pc "plain text"',
     False, None),
    ("claude_cluster_pq_clean_allows",
     'claude -pq "plain text"',
     False, None),

    # ─── round 2.2: dirty prompt without p (ALLOW) ───────────────────
    ("claude_dash_c_dollar_paren_id_allows",
     'claude -c "$(id)"',
     False, None),
    ("claude_dash_d_dirty_prompt_allows",
     'claude -d "$(id)"',
     False, None),
    ("claude_dash_h_dirty_prompt_allows",
     'claude -h "$(id)"',
     False, None),
    ("claude_dash_n_dirty_prompt_allows",
     'claude -n "$(id)"',
     False, None),
    ("claude_dash_w_dirty_prompt_allows",
     'claude -w "$(id)"',
     False, None),
    ("claude_dash_r_dirty_prompt_allows",
     'claude -r "$(id)"',
     False, None),
    ("claude_dash_v_dirty_prompt_allows",
     'claude -v "$(id)"',
     False, None),
    ("claude_dash_cdh_dirty_prompt_allows",
     'claude -cdh "$(id)"',
     False, None),

    # ─── round 2.2: sudo variants with dirty prompt (DENY) ───────────
    ("sudo_n_claude_p_dirty_denied",
     'sudo -n claude -p "$(id)"',
     True, "claude"),
    ("sudo_E_claude_p_dirty_denied",
     'sudo -E claude -p "$(id)"',
     True, "claude"),
    ("sudo_uroot_claude_p_dirty_denied",
     'sudo -uroot claude -p "$(id)"',
     True, "claude"),
    ("sudo_user_equals_root_claude_p_dirty_denied",
     'sudo --user=root claude -p "$(id)"',
     True, "claude"),
    ("sudo_u_root_claude_p_dirty_denied",
     'sudo -u root claude -p "$(id)"',
     True, "claude"),

    # ─── round 2.2: sudo variants with clean prompt (ALLOW) ──────────
    ("sudo_n_claude_p_clean_allows",
     'sudo -n claude -p "plain text"',
     False, None),
    ("sudo_E_claude_p_clean_allows",
     'sudo -E claude -p "plain text"',
     False, None),
    ("sudo_uroot_claude_p_clean_allows",
     'sudo -uroot claude -p "plain text"',
     False, None),
    ("sudo_user_equals_root_claude_p_clean_allows",
     'sudo --user=root claude -p "plain text"',
     False, None),
    ("sudo_u_root_clean_allows",
     'sudo -u root claude -p "plain text"',
     False, None),

    # ─── round 2.2: flag equals with dirty prompt (DENY) ─────────────
    ("claude_p_equals_dirty_denied",
     'claude -p="$(id)"',
     True, "claude"),
    ("claude_p_equals_clean_allows",
     'claude -p="plain text"',
     False, None),
    ("claude_print_equals_dirty_denied",
     'claude --print="$(id)"',
     True, "claude"),
]


class BashGuardCaseTests(unittest.TestCase):
    """One test per CASES row, generated below."""


def _make_case_test(command, expect_deny, expect_substr):
    def test(self):
        r = run_bash(command)
        if expect_deny:
            self.assertEqual(r.returncode, 2,
                              "command=%r stdout=%r stderr=%r"
                              % (command, r.stdout, r.stderr))
            if expect_substr:
                self.assertIn(expect_substr, r.stderr,
                               "stderr=%r" % r.stderr)
        else:
            self.assertEqual(r.returncode, 0,
                              "command=%r stdout=%r stderr=%r"
                              % (command, r.stdout, r.stderr))
            self.assertEqual(r.stderr, "",
                             "command=%r stderr=%r" % (command, r.stderr))
    return test


for _name, _command, _deny, _substr in CASES:
    setattr(BashGuardCaseTests, "test_" + _name,
            _make_case_test(_command, _deny, _substr))


class BashGuardPayloadShapeTests(unittest.TestCase):
    """The hook contract around the Bash-only filter and fail-open parsing,
    independent of any one command's shell syntax."""

    def test_non_bash_tool_is_ignored_even_with_a_dangerous_command(self):
        r = run_hook({"tool_name": "Read",
                       "tool_input": {"command": "cat <<EOF\n`id`\nEOF\n"}})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, "")

    def test_malformed_json_fails_open_with_a_note(self):
        r = run_hook_raw("{not json")
        self.assertEqual(r.returncode, 0)
        self.assertIn("could not parse", r.stderr)

    def test_json_not_an_object_fails_open_with_a_note(self):
        r = run_hook_raw("[1, 2, 3]")
        self.assertEqual(r.returncode, 0)
        self.assertIn("not a JSON object", r.stderr)

    def test_bash_tool_without_tool_input_fails_open_with_a_note(self):
        r = run_hook({"tool_name": "Bash"})
        self.assertEqual(r.returncode, 0)
        self.assertIn("missing or malformed", r.stderr)

    def test_bash_tool_without_command_allows_silently(self):
        r = run_hook({"tool_name": "Bash", "tool_input": {}})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, "")

    def test_empty_command_allows_silently(self):
        r = run_bash("")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, "")

    def test_non_utf8_stdin_fails_open_without_traceback(self):
        r = run_hook_bytes(b"\xff\xfe\x00\x80")
        self.assertEqual(r.returncode, 0)
        stderr_text = r.stderr.decode("utf-8", errors="replace")
        self.assertNotIn("Traceback", stderr_text)
        self.assertNotIn("UnicodeDecodeError", stderr_text)

    def test_invalid_utf8_byte_in_command_denied_and_allowed(self):
        dirty = b'{"tool_name":"Bash","tool_input":{"command":"claude -p \\"$(id)\\" \xff"}}'
        r_dirty = run_hook_bytes(dirty)
        self.assertEqual(r_dirty.returncode, 2)
        self.assertIn("claude", r_dirty.stderr.decode("utf-8", errors="replace"))

        clean = b'{"tool_name":"Bash","tool_input":{"command":"claude -p \\"plain prompt\\" \xff"}}'
        r_clean = run_hook_bytes(clean)
        self.assertEqual(r_clean.returncode, 0)
        self.assertEqual(r_clean.stderr, b"")

    def test_oversized_payload_fails_open_with_note(self):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "x" * (2 * 1024 * 1024)}})
        r = run_hook_raw(payload)
        self.assertEqual(r.returncode, 0)
        self.assertIn("1 MiB", r.stderr)

    def test_raw_nul_byte_in_json_fails_open(self):
        r = run_hook_bytes(b'{"tool_name":"Bash","tool_input":{"command":"\x00"}}')
        self.assertEqual(r.returncode, 0)
        self.assertIn("could not parse", r.stderr.decode("utf-8", errors="replace"))

    def test_shell_c_depth_4_nesting_allowed_with_note(self):
        cmd = 'bash -c "bash -c \\"bash -c \\\\\\\"bash -c \'claude -p \\\\\\\\\\\\\\\"$(id)\\\\\\\\\\\\\\\"\'\\\\\\\"\\\""'
        r = run_bash(cmd)
        self.assertEqual(r.returncode, 0)
        self.assertIn("nesting", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

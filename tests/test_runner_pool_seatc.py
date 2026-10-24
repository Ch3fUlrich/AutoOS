import io
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
import runner_pool_gate as gate
import runner_pool_core as rpc

BASE = {'client': 'api', 'model': 'm', 'kind': 'k', 'task_type': 't', 'title': 'T'}


class TestSeatCFindings(unittest.TestCase):
    # finding: card.paths had no confinement
    def test_paths_escaping_the_pool_are_rejected(self):
        bad = ['..\\..\\x', '../x', 'a/../../x', '/etc/passwd', '\\\\host\\share\\x',
               'C:\\secrets\\k', 'C:/k', 'CON', 'nul.txt', 'COM1', 'foo.', 'foo ',
               'a/./b/../../..', '', '~/x']
        for p in bad:
            with self.assertRaises(gate.GateError, msg=repr(p)):
                gate.validate_card(dict(BASE, paths=[p]))

    def test_plain_relative_paths_are_accepted(self):
        card = gate.validate_card(dict(BASE, paths=['tools/a.py', 'docs/plans/x-y.md', 'a.b_c']))
        self.assertEqual(card['paths'], ['tools/a.py', 'docs/plans/x-y.md', 'a.b_c'])

    def test_paths_must_be_a_list_of_strings(self):
        for p in ('tools/a.py', [1], [['x']], None):
            with self.assertRaises(gate.GateError, msg=repr(p)):
                gate.validate_card(dict(BASE, paths=p))

    # finding: strip_env was case-sensitive (NT env is case-insensitive)
    def test_strip_env_is_case_insensitive(self):
        env = {'autoos_admission_off': '1', 'Autoos_Admission_Off': '1',
               'AUTOOS_ADMISSION_OFF': '1', 'KEEP': 'x'}
        self.assertEqual(rpc.strip_env(env), {'KEEP': 'x'})
        self.assertEqual(len(env), 4)  # original untouched

    # finding: read_capped blocked until cap+1 bytes or EOF
    def test_read_capped_times_out_on_a_stalled_channel(self):
        r, w = os.pipe()
        stream = os.fdopen(r, 'rb')
        # LIFO: close the write end first (EOF frees the reader thread), then the stream
        self.addCleanup(stream.close)
        self.addCleanup(os.close, w)
        os.write(w, b'0123456789')  # 10 bytes, no EOF
        done = []
        def run():
            try:
                gate.read_capped(stream, 65536, timeout=0.3)
            except gate.GateError as e:
                done.append(str(e))
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(5)
        self.assertFalse(t.is_alive(), 'read_capped hung')
        self.assertEqual(done, ['read timeout'])

    def test_read_capped_still_reads_normal_input(self):
        self.assertEqual(gate.read_capped(io.BytesIO(b'abc'), 10), b'abc')
        with self.assertRaises(gate.GateError):
            gate.read_capped(io.BytesIO(b'x' * 11), 10)


if __name__ == '__main__':
    unittest.main()

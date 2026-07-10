import unittest
import sys
import os
import importlib.util

# Mock sys.argv to prevent argparse from failing or exiting during import
old_argv = sys.argv
sys.argv = ['eduroam-linux-Universitat_Basel-UniBasel.py']

try:
    # Import the module dynamically since the filename contains hyphens
    file_path = os.path.join(os.path.dirname(__file__), 'eduroam-linux-Universitat_Basel-UniBasel.py')
    spec = importlib.util.spec_from_file_location('eduroam_module', file_path)
    eduroam = importlib.util.module_from_spec(spec)
    sys.modules['eduroam_module'] = eduroam
    spec.loader.exec_module(eduroam)
finally:
    # Restore sys.argv
    sys.argv = old_argv

class TestByteToString(unittest.TestCase):
    def test_empty_list(self):
        """Test with an empty list."""
        self.assertEqual(eduroam.byte_to_string([]), "")

    def test_valid_ascii(self):
        """Test with valid ASCII bytes."""
        self.assertEqual(eduroam.byte_to_string([101, 100, 117, 114, 111, 97, 109]), "eduroam")

    def test_null_byte(self):
        """Test with a null byte."""
        self.assertEqual(eduroam.byte_to_string([0]), "\x00")

    def test_max_byte(self):
        """Test with the maximum byte value (255)."""
        self.assertEqual(eduroam.byte_to_string([255]), "\xff")

    def test_unicode_chars(self):
        """Test with unicode characters (if chr supports > 255)."""
        self.assertEqual(eduroam.byte_to_string([8364]), "€")

    def test_invalid_type(self):
        """Test that invalid types raise TypeError."""
        with self.assertRaises(TypeError):
            eduroam.byte_to_string([None])
        with self.assertRaises(TypeError):
            eduroam.byte_to_string(["a"])

    def test_out_of_range(self):
        """Test that out-of-range values raise ValueError."""
        with self.assertRaises(ValueError):
            eduroam.byte_to_string([-1])
        with self.assertRaises(ValueError):
            eduroam.byte_to_string([0x110000])

if __name__ == '__main__':
    unittest.main()

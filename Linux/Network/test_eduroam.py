import unittest
import importlib.util
import sys
import os

class TestEduroam(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Mock sys.argv to prevent argparse from failing or exiting during import
        cls.original_argv = sys.argv
        sys.argv = ['eduroam-linux-Universitat_Basel-UniBasel.py']

        try:
            # Load the module dynamically due to hyphens in the filename
            module_name = 'eduroam'
            file_path = os.path.join(os.path.dirname(__file__), 'eduroam-linux-Universitat_Basel-UniBasel.py')
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            cls.eduroam_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.eduroam_module)
        finally:
            # Restore original sys.argv
            sys.argv = cls.original_argv

    def test_byte_to_string_happy_path(self):
        # Test basic conversion
        self.assertEqual(self.eduroam_module.byte_to_string([104, 101, 108, 108, 111]), "hello")

    def test_byte_to_string_empty(self):
        # Test empty list
        self.assertEqual(self.eduroam_module.byte_to_string([]), "")

    def test_byte_to_string_unicode(self):
        # Test unicode characters beyond ASCII
        self.assertEqual(self.eduroam_module.byte_to_string([8364]), "€")

    def test_byte_to_string_error_conditions(self):
        # Test type error if list contains non-integers
        with self.assertRaises(TypeError):
            self.eduroam_module.byte_to_string(["not_an_int"])

if __name__ == '__main__':
    unittest.main()

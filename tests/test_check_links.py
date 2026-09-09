import importlib.util
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# Load the check-links.py script as a module
script_path = os.path.join(os.path.dirname(__file__), "check-links.py")
spec = importlib.util.spec_from_file_location("check_links", script_path)
check_links = importlib.util.module_from_spec(spec)
sys.modules["check_links"] = check_links
spec.loader.exec_module(check_links)

class TestCheckLinks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = self.temp_dir.name

        # Create some files for testing

        # docs/ dir
        os.makedirs(os.path.join(self.root, "docs"))

        # README.md
        with open(os.path.join(self.root, "README.md"), "w", encoding="utf-8") as f:
            f.write("""
            [Valid link](docs/test1.md)
            [Valid link 2](./docs/test1.md)
            [Broken link](docs/missing.md)
            [Skipped http](http://example.com)
            [Skipped https](https://example.com)
            [Skipped mailto](mailto:test@example.com)
            [Skipped anchor](#anchor)
            [Anchor with file](docs/test1.md#anchor)
            [Link to image](docs/image.png)
            [Link with weird path](docs/../docs/test1.md)
            """)

        # docs/test1.md
        with open(os.path.join(self.root, "docs", "test1.md"), "w", encoding="utf-8") as f:
            f.write("[Back to root](../README.md)")

        # docs/image.png
        with open(os.path.join(self.root, "docs", "image.png"), "w", encoding="utf-8") as f:
            f.write("fake image")

        # Ignored file in git dir
        os.makedirs(os.path.join(self.root, ".git"))
        with open(os.path.join(self.root, ".git", "README.md"), "w", encoding="utf-8") as f:
            f.write("[Should not be checked](missing.md)")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_markdown_files(self):
        files = check_links.markdown_files(self.root)
        self.assertEqual(len(files), 2)
        # Convert path separators for cross-platform compatibility, though check-links uses relpath
        # relpath uses os.sep
        expected = sorted(["README.md", os.path.join("docs", "test1.md")])
        self.assertEqual(files, expected)

    def test_broken_links(self):
        problems = check_links.broken_links(self.root)

        # Should only find the missing.md link
        self.assertEqual(len(problems), 1)
        self.assertIn("README.md: [Broken link] -> docs/missing.md", problems[0])

    @patch('sys.argv', ['check-links.py', '.'])
    @patch('check_links.print')
    def test_main_with_problems(self, mock_print):
        # Override sys.argv to use our temp directory
        with patch('sys.argv', ['check-links.py', self.root]):
            return_code = check_links.main()

            # Should return 1 because of broken link
            self.assertEqual(return_code, 1)

            # Should print error messages
            mock_print.assert_any_call("1 broken link(s) across 2 file(s):")
            mock_print.assert_any_call("  README.md: [Broken link] -> docs/missing.md")

    @patch('check_links.print')
    def test_main_without_problems(self, mock_print):
        # Create a temp dir with no broken links
        with tempfile.TemporaryDirectory() as good_dir:
            with open(os.path.join(good_dir, "README.md"), "w", encoding="utf-8") as f:
                f.write("[Good link](README.md)")

            with patch('sys.argv', ['check-links.py', good_dir]):
                return_code = check_links.main()

                # Should return 0
                self.assertEqual(return_code, 0)
                mock_print.assert_called_with("all relative links resolve (1 files checked)")

if __name__ == "__main__":
    unittest.main()

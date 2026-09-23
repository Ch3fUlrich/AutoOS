import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import URLError

# Path to the script we want to test
script_path = os.path.join(os.path.dirname(__file__), "populate-embeddings.py")

# Import the script as a module
spec = importlib.util.spec_from_file_location("populate_embeddings", script_path)
populate_embeddings = importlib.util.module_from_spec(spec)
sys.modules["populate_embeddings"] = populate_embeddings
spec.loader.exec_module(populate_embeddings)

class TestPopulateEmbeddings(unittest.TestCase):
    def test_setup(self):
        self.assertTrue(hasattr(populate_embeddings, "embed"))

    @patch("urllib.request.urlopen")
    @patch("urllib.request.Request")
    def test_embed_happy_path(self, mock_request, mock_urlopen):
        # Setup mock for urllib.request.Request
        mock_req_instance = MagicMock()
        mock_request.return_value = mock_req_instance

        # Setup mock for urllib.request.urlopen context manager
        expected_embedding = [0.1, 0.2, 0.3]
        response_data = json.dumps({
            "data": [{"embedding": expected_embedding}]
        }).encode("utf-8")

        import io
        mock_response = io.BytesIO(response_data)

        mock_context_manager = MagicMock()
        mock_context_manager.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_context_manager

        # Execute
        result = populate_embeddings.embed(
            ollama="http://test-ollama",
            model="test-model",
            text="test text",
            key="test-key"
        )

        # Assert result matches expectation
        self.assertEqual(result, expected_embedding)

        # Assert Request was instantiated correctly
        mock_request.assert_called_once_with(
            "http://test-ollama/v1/embeddings",
            data=json.dumps({"model": "test-model", "input": "test text"}).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"}
        )

        # Assert urlopen was called with the Request object and timeout
        mock_urlopen.assert_called_once_with(mock_req_instance, timeout=60)

    @patch("urllib.request.urlopen")
    @patch("urllib.request.Request")
    def test_embed_timeout_error(self, mock_request, mock_urlopen):
        mock_req_instance = MagicMock()
        mock_request.return_value = mock_req_instance

        # Mock urlopen to raise URLError
        mock_urlopen.side_effect = URLError("timeout")

        # Execute and expect exception to bubble up
        with self.assertRaises(URLError):
            populate_embeddings.embed(
                ollama="http://test-ollama",
                model="test-model",
                text="test text",
                key="test-key"
            )

        mock_urlopen.assert_called_once_with(mock_req_instance, timeout=60)

if __name__ == "__main__":
    unittest.main()

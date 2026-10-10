import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Add the current directory to sys.path so we can import pull_graph
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pull_graph import export, mutate

class TestPullGraph(unittest.TestCase):
    @patch('pull_graph.urllib.request.urlopen')
    @patch('pull_graph.urllib.request.Request')
    def test_export_success(self, mock_request_cls, mock_urlopen):
        # Setup mock response
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"id": 1, "name": "node1"}\n{"id": 2, "name": "node2"}\n'
        mock_urlopen.return_value = mock_response

        # Setup mock Request to just return itself so we can verify it's passed to urlopen
        mock_req_instance = MagicMock()
        mock_request_cls.return_value = mock_req_instance

        # Call the function
        result = export("http://example.com/", "my_token", "my_graph")

        # Verify the result
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], {"id": 1, "name": "node1"})
        self.assertEqual(result[1], {"id": 2, "name": "node2"})

        # Verify Request arguments
        mock_request_cls.assert_called_once_with(
            "http://example.com/graphs/my_graph/export",
            data=b"{}",
            headers={"Authorization": "Bearer my_token", "content-type": "application/json",
                     "omnigraph-http-api": "0.13"},
            method="POST"
        )

        # Verify urlopen arguments
        mock_urlopen.assert_called_once_with(mock_req_instance, timeout=300)

    @patch('pull_graph.urllib.request.urlopen')
    def test_export_empty_lines_and_whitespace(self, mock_urlopen):
        # Setup mock response with empty lines and whitespace
        mock_response = MagicMock()
        mock_response.read.return_value = b'\n  \n{"id": 1}\n\n\t\n{"id": 2}\n  '
        mock_urlopen.return_value = mock_response

        result = export("http://example.com", "t", "g")

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], {"id": 1})
        self.assertEqual(result[1], {"id": 2})

    @patch('pull_graph.urllib.request.urlopen')
    @patch('pull_graph.urllib.request.Request')
    def test_export_url_formatting(self, mock_request_cls, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"id": 1}\n'
        mock_urlopen.return_value = mock_response

        # Call with a URL that doesn't have a trailing slash
        export("http://example.com/api", "t", "g")

        # Check that it formed correctly
        mock_request_cls.assert_called_once_with(
            "http://example.com/api/graphs/g/export",
            data=b"{}",
            headers={"Authorization": "Bearer t", "content-type": "application/json",
                     "omnigraph-http-api": "0.13"},
            method="POST"
        )

        mock_request_cls.reset_mock()

        # Call with a URL that has multiple trailing slashes
        export("http://example.com/api///", "t", "g")

        # Check that it forms correctly (rstrip removes all trailing slashes)
        mock_request_cls.assert_called_once_with(
            "http://example.com/api/graphs/g/export",
            data=b"{}",
            headers={"Authorization": "Bearer t", "content-type": "application/json",
                     "omnigraph-http-api": "0.13"},
            method="POST"
        )

    @patch('pull_graph.urllib.request.urlopen')
    @patch('pull_graph.urllib.request.Request')
    def test_export_sends_contract_header(self, mock_request_cls, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"id": 1}\n'
        mock_urlopen.return_value = mock_response

        export("http://example.com", "my_token", "my_graph")

        url = mock_request_cls.call_args.args[0]
        headers = mock_request_cls.call_args.kwargs["headers"]
        self.assertEqual("http://example.com/graphs/my_graph/export", url)
        self.assertEqual("0.13", headers["omnigraph-http-api"])
        self.assertEqual("Bearer my_token", headers["Authorization"])
        self.assertEqual("application/json", headers["content-type"])

    @patch('pull_graph.urllib.request.urlopen')
    @patch('pull_graph.urllib.request.Request')
    def test_mutate_sends_contract_header(self, mock_request_cls, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{}'
        mock_urlopen.return_value.__enter__.return_value = mock_response

        mutate("http://example.com", "my_token", "my_graph", "query purge() { }")

        url = mock_request_cls.call_args.args[0]
        headers = mock_request_cls.call_args.kwargs["headers"]
        self.assertEqual("http://example.com/graphs/my_graph/mutate", url)
        self.assertEqual("0.13", headers["omnigraph-http-api"])
        self.assertEqual("Bearer my_token", headers["Authorization"])
        self.assertEqual("application/json", headers["content-type"])

if __name__ == '__main__':
    unittest.main()

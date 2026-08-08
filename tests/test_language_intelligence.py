from unittest.mock import MagicMock, patch

import pytest

from nexus.language_intelligence import LanguageServicePool, LSPClient, LSPError, TreeSitterAdapter


@pytest.fixture
def mock_subprocess():
    with patch("nexus.process_gateway.ProcessExecutionGateway.popen") as mock_popen:
        process = MagicMock()
        process.poll.return_value = None
        process.stdin = MagicMock()
        process.stdout = MagicMock()
        # Ensure read_loop terminates naturally in tests
        process.stdout.readline.side_effect = [b"Content-Length: 10\r\n", b"\r\n", b"", b""]
        process.stdout.read.return_value = b'{"id": 1, "result": "ok"}'
        mock_popen.return_value = process
        yield mock_popen


def test_lsp_client_lifecycle(mock_subprocess, tmp_path):
    client = LSPClient(tmp_path, "python", command=("dummy-lsp",), timeout_seconds=1.0)

    # Mocking _send to put a response in the queue immediately
    def fake_send(msg):
        if "id" in msg:
            client._pending[msg["id"]].put({"id": msg["id"], "result": "ok"})

    with patch.object(client, "_send", side_effect=fake_send):
        client.start()
        mock_subprocess.assert_called_once()
        # Test close
        client.close()


def test_lsp_client_request(tmp_path):
    client = LSPClient(tmp_path, "python", command=("dummy-lsp",), timeout_seconds=1.0)
    client.process = MagicMock()
    client.process.poll.return_value = None

    # mock _send to immediately put a response in the queue
    def fake_send(msg):
        if "id" in msg:
            client._pending[msg["id"]].put({"id": msg["id"], "result": [{"name": "fake_symbol"}]})

    with patch.object(client, "_send", side_effect=fake_send):
        (tmp_path / "test.py").write_text("def fake_symbol(): pass")
        res = client.document_symbols(tmp_path / "test.py")
        assert len(res) == 1
        assert res[0]["name"] == "fake_symbol"


def test_language_service_pool(tmp_path):
    pool = LanguageServicePool(tmp_path)
    with patch("nexus.language_intelligence.LSPClient.discover", return_value=("dummy",)):
        with patch("nexus.language_intelligence.LSPClient.start") as mock_start:
            with patch("nexus.language_intelligence.LSPClient.close") as mock_close:
                client1 = pool.client("python")
                client2 = pool.client("python")
                assert client1 is client2
                mock_start.assert_called_once()

                pool.close()
                mock_close.assert_called_once()


def test_treesitter_graceful_degradation():
    adapter = TreeSitterAdapter()
    if not adapter.available:
        with pytest.raises(LSPError):
            adapter.symbols("def a(): pass", "python")

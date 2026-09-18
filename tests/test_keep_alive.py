import http.client
import unittest

from keep_alive import KEEP_ALIVE_TEXT, keep_alive, stop_keep_alive


class KeepAliveTest(unittest.TestCase):
    def test_root_returns_running_status(self):
        handle = keep_alive(host="127.0.0.1", port=0)
        server, _ = handle
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_address[1],
                timeout=2,
            )
            connection.request("GET", "/")
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            connection.close()
        finally:
            stop_keep_alive(handle)

        self.assertEqual(response.status, 200)
        self.assertEqual(body, KEEP_ALIVE_TEXT)

    def test_unknown_path_returns_not_found(self):
        handle = keep_alive(host="127.0.0.1", port=0)
        server, _ = handle
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_address[1],
                timeout=2,
            )
            connection.request("GET", "/health")
            response = connection.getresponse()
            response.read()
            connection.close()
        finally:
            stop_keep_alive(handle)

        self.assertEqual(response.status, 404)
import unittest

from fastapi.testclient import TestClient

from main import app


class MainRouteTests(unittest.TestCase):
    def test_favicon_probe_returns_no_content(self):
        response = TestClient(app).get("/favicon.ico")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")


if __name__ == "__main__":
    unittest.main()

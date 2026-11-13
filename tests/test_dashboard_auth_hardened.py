import time
import unittest
from fastapi.testclient import TestClient
from dashboard.server import DashboardServer, _TOKEN_TTL_SECONDS
from memory import config_manager


class TestDashboardAuthHardened(unittest.TestCase):

    def setUp(self):
        # Ensure default loopback config
        config_manager.set_dashboard_allow_lan(False)
        self.server = DashboardServer()
        self.client = TestClient(self.server.app, client=("127.0.0.1", 50000))

    def tearDown(self):
        config_manager.set_dashboard_allow_lan(False)

    def test_security_headers_present(self):
        resp = self.client.get("/login")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("x-content-type-options"), "nosniff")
        self.assertIn("no-store", resp.headers.get("cache-control", ""))
        self.assertEqual(resp.headers.get("referrer-policy"), "strict-origin-when-cross-origin")
        self.assertIn("default-src", resp.headers.get("content-security-policy", ""))

    def test_ip_restriction_blocks_external_ip_when_lan_disabled(self):
        # External client IP
        external_client = TestClient(self.server.app, client=("203.0.113.19", 50000))
        resp = external_client.get("/login")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("Forbidden", resp.json().get("error", ""))

    def test_ip_restriction_allows_lan_when_enabled(self):
        config_manager.set_dashboard_allow_lan(True)
        lan_client = TestClient(self.server.app, client=("192.168.1.150", 50000))
        resp = lan_client.get("/login")
        self.assertEqual(resp.status_code, 200)

    def test_login_success_and_cookie_generation(self):
        key = self.server.new_key(expiry_secs=60)
        resp = self.client.post("/login", json={"pin": key})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("ok"))
        token = data.get("token")
        dev_tok = data.get("device_token")
        self.assertTrue(bool(token))
        self.assertTrue(bool(dev_tok))

        # Check cookie
        cookie_header = resp.headers.get("set-cookie", "")
        self.assertIn("jarvis_auth_token=", cookie_header)
        self.assertIn("HttpOnly", cookie_header)
        self.assertIn("SameSite=strict", cookie_header)

        # Authenticated endpoint works with bearer token or cookie
        cmd_resp = self.client.post(
            "/api/command",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": "status check"},
        )
        self.assertEqual(cmd_resp.status_code, 200)

    def test_invalid_pin_and_rate_limiting(self):
        # 1 invalid attempt
        resp1 = self.client.post("/login", json={"pin": "WRONGPIN"})
        self.assertEqual(resp1.status_code, 401)

        # Multiple failures trigger lockout
        for _ in range(6):
            self.client.post("/login", json={"pin": "WRONGPIN"})
        
        locked_resp = self.client.post("/login", json={"pin": "WRONGPIN"})
        self.assertEqual(locked_resp.status_code, 429)
        self.assertIn("Too many attempts", locked_resp.json().get("error", ""))

    def test_device_pairing_and_revocation(self):
        key = self.server.new_key(expiry_secs=60)
        login_resp = self.client.post("/login", json={"pin": key})
        token = login_resp.json()["token"]
        dev_tok = login_resp.json()["device_token"]

        # List devices
        dev_list_resp = self.client.get(
            "/api/devices",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(dev_list_resp.status_code, 200)
        devices = dev_list_resp.json().get("devices", [])
        self.assertTrue(len(devices) >= 1)
        device_id = devices[0]["device_id"]

        # Revoke device by id
        revoke_resp = self.client.post(
            "/api/devices/revoke",
            headers={"Authorization": f"Bearer {token}"},
            json={"device_id": device_id},
        )
        self.assertEqual(revoke_resp.status_code, 200)
        self.assertEqual(revoke_resp.json().get("revoked"), 1)

        # Device login with revoked device token should fail
        relogin = self.client.post("/api/device-login", json={"device_token": dev_tok})
        self.assertEqual(relogin.status_code, 401)

    def test_token_expiry(self):
        key = self.server.new_key(expiry_secs=60)
        login_resp = self.client.post("/login", json={"pin": key})
        token = login_resp.json()["token"]

        # Artificially age the token
        self.server._tokens[token] = time.time() - (_TOKEN_TTL_SECONDS + 10)

        cmd_resp = self.client.post(
            "/api/command",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": "expired test"},
        )
        self.assertEqual(cmd_resp.status_code, 401)

    def test_health_check_endpoint(self):
        """Verify /health endpoint returns service health, security and session count."""
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["service"], "JARVIS Dashboard")
        self.assertIn("tls_enabled", data)
        self.assertIn("active_sessions", data)
        self.assertIn("allow_lan", data)


if __name__ == "__main__":
    unittest.main()

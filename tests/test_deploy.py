import unittest
from unittest.mock import patch

from cogs.deploy import (
    _find_recent_deploy_id,
    _post_deploy_hook,
    _render_status_message,
)


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self._body = payload

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class DeployTests(unittest.TestCase):
    def test_post_hook_reads_deploy_id(self):
        response = _Response(200, b'{"deployId":"dep-123"}')
        with patch("cogs.deploy.urlopen", return_value=response):
            status, deploy_id = _post_deploy_hook(
                "https://api.render.com/deploy/srv-123?key=secret"
            )

        self.assertEqual(status, 200)
        self.assertEqual(deploy_id, "dep-123")

    def test_find_recent_deploy_id_from_render_list(self):
        deploy_id = _find_recent_deploy_id(
            [
                {
                    "deploy": {
                        "id": "dep-old",
                        "trigger": "manual",
                        "createdAt": "2026-08-12T00:00:00Z",
                    }
                },
                {
                    "deploy": {
                        "id": "dep-new",
                        "trigger": "deploy_hook",
                        "createdAt": "2026-08-13T03:00:00Z",
                    }
                },
            ],
            1786560000,
        )

        self.assertEqual(deploy_id, "dep-new")

    def test_render_status_message_distinguishes_live_and_failed(self):
        success, success_embed = _render_status_message("live", "dep-live")
        failed, failed_embed = _render_status_message("build_failed", "dep-failed")

        self.assertTrue(success)
        self.assertIn("hoàn tất thành công", success_embed.description)
        self.assertFalse(failed)
        self.assertIn("thất bại", failed_embed.description)


if __name__ == "__main__":
    unittest.main()

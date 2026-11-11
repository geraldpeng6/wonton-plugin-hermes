"""没有模型时的提示：只按说话人的语言回一条；他的语言取不到时退到主人，再退英文。

起一个本地 HTTP 假核心：模型的列表，和按人回语言的 MCP 工具（和核心一样用 Streamable HTTP）。
"""

import json
import os
import sys
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))

# 插件在 Hermes 里才读得到这个模块；测试用固定的主人和 provider。
config = types.ModuleType("hermes_cli.config")
config.load_config_readonly = lambda: {"model": {"provider": "custom:magpie"}}
hermes_cli = types.ModuleType("hermes_cli")
hermes_cli.config = config
sys.modules.setdefault("hermes_cli", hermes_cli)
sys.modules.setdefault("hermes_cli.config", config)

from wonton import locale, missing_model_hint  # noqa: E402
from wonton import texts  # noqa: E402

OWNER = "@owner:example.org"


class Fake:
    def __init__(self):
        self.models = []
        self.people = {OWNER: ["en"]}
        self.calls = []


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    def reply(self, status=200, body=b"", content_type="application/json", headers=None):
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def event(self, message, headers=None):
        # 和核心一样先发一条只有 id、retry 的空事件。
        body = f"data: \nid: 0\nretry: 3000\n\nevent: message\ndata: {json.dumps(message)}\n\n".encode()
        self.reply(body=body, content_type="text/event-stream", headers=headers)

    def do_GET(self):
        fake = self.server.fake
        if self.path != "/v1/models":
            return self.reply(404)
        if fake.models is None:
            return self.reply(503)
        self.reply(body=json.dumps({"data": [{"id": model} for model in fake.models]}).encode())

    def do_DELETE(self):
        self.reply()

    def do_POST(self):
        fake = self.server.fake
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method = body.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {}, "serverInfo": {"name": "fake", "version": "1"}}
            return self.event({"jsonrpc": "2.0", "id": body["id"], "result": result}, {"Mcp-Session-Id": "s"})
        if method == "notifications/initialized":
            return self.reply(202)
        if method != "tools/call":
            return self.reply(400)
        args = body["params"]["arguments"]
        fake.calls.append(args)
        tags = fake.people.get(args.get("user_id") or OWNER)
        if tags is None:
            result = {"isError": True, "content": [{"type": "text", "text": "not_found: no such person"}]}
        else:
            value = {"locale": tags[0] if tags else None, "languages": tags}
            result = {"content": [{"type": "text", "text": json.dumps(value)}]}
        self.event({"jsonrpc": "2.0", "id": body["id"], "result": result})


class HintCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        locale._cache.clear()
        self.fake = Fake()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.fake = self.fake
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.vars = {
            "WONTON_MODEL_BASE_URL": f"{base}/v1",
            "WONTON_MODEL_API_KEY": "gateway-key",
            "WONTON_MODEL_DEFAULT": "group/wonton-default",
            "WONTON_MCP_URL": f"{base}/mcp/token",
            "WONTON_OWNER_NAME": "owner",
        }
        patch = mock.patch.dict(os.environ, self.vars)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    async def hint(self, text="你好", user=OWNER, platform="matrix"):
        return await missing_model_hint(text=text, source={"platform": platform, "user_id": user})

    async def test_english_person_gets_english_only(self):
        self.fake.people[OWNER] = ["en"]
        reply = (await self.hint())["reply"]
        self.assertEqual(reply, texts.NO_MODELS["en"])
        self.assertIn("Apps → Models", reply)
        self.assertNotIn("\n", reply)

    async def test_simplified_chinese(self):
        self.fake.people[OWNER] = ["zh-Hans", "en"]
        reply = (await self.hint())["reply"]
        self.assertEqual(reply, texts.NO_MODELS["zh-Hans"])
        self.assertIn("应用 → 模型设置", reply)
        self.assertNotIn("\n", reply)

    async def test_traditional_from_region_tag(self):
        self.fake.people[OWNER] = ["zh-TW"]
        reply = (await self.hint())["reply"]
        self.assertEqual(reply, texts.NO_MODELS["zh-Hant"])
        self.assertIn("應用程式", reply)

    async def test_japanese(self):
        self.fake.people[OWNER] = ["ja-JP", "en"]
        reply = (await self.hint())["reply"]
        self.assertEqual(reply, texts.NO_MODELS["ja"])
        self.assertIn("アプリ → Models", reply)

    async def test_unsupported_language_falls_back_to_english(self):
        self.fake.people[OWNER] = ["fr-FR"]
        self.assertEqual((await self.hint())["reply"], texts.NO_MODELS["en"])

    async def test_family_without_record_uses_owner_language(self):
        self.fake.people[OWNER] = ["zh-Hant"]
        reply = (await self.hint(user="@alice:example.org"))["reply"]
        self.assertEqual(reply, texts.NO_MODELS["zh-Hant"])
        self.assertEqual(self.fake.calls, [{"user_id": "@alice:example.org"}, {}])

    async def test_owner_without_record_asks_once_and_speaks_english(self):
        reply = (await self.hint())["reply"]
        self.assertEqual(reply, texts.NO_MODELS["en"])
        self.assertEqual(self.fake.calls, [{"user_id": OWNER}])

    async def test_non_matrix_sender_uses_owner_language(self):
        self.fake.people[OWNER] = ["ja"]
        reply = (await self.hint(user="12345", platform="telegram"))["reply"]
        self.assertEqual(reply, texts.NO_MODELS["ja"])
        self.assertEqual(self.fake.calls, [{}])

    async def test_gateway_without_model_vars_still_localizes(self):
        self.fake.people[OWNER] = ["zh-Hans"]
        del os.environ["WONTON_MODEL_BASE_URL"]
        del os.environ["WONTON_MODEL_API_KEY"]
        del os.environ["WONTON_MODEL_DEFAULT"]
        self.assertEqual((await self.hint())["reply"], texts.NO_MODELS["zh-Hans"])

    async def test_default_model_present_replies_nothing(self):
        self.fake.models = ["group/wonton-default"]
        self.assertIsNone(await self.hint())
        self.assertEqual(self.fake.calls, [])

    async def test_gateway_unreachable_replies_nothing(self):
        self.fake.models = None
        self.assertIsNone(await self.hint())
        self.assertEqual(self.fake.calls, [])

    async def test_missing_default_names_the_group(self):
        self.fake.models = ["other"]
        self.fake.people[OWNER] = ["zh-Hans"]
        reply = (await self.hint())["reply"]
        self.assertEqual(reply, texts.NO_DEFAULT["zh-Hans"].format(model="group/wonton-default", group="wonton-default"))
        self.assertIn("名为 wonton-default", reply)
        self.assertEqual(reply.count("group/"), 1)

    async def test_command_is_left_alone(self):
        self.assertIsNone(await self.hint(text=" /model"))
        self.assertEqual(self.fake.calls, [])

    async def test_language_is_cached_per_person(self):
        self.fake.people[OWNER] = ["ja"]
        for _ in range(3):
            self.assertEqual((await self.hint())["reply"], texts.NO_MODELS["ja"])
        self.assertEqual(len(self.fake.calls), 1)

    async def test_core_mcp_unreachable_falls_back_to_english(self):
        os.environ["WONTON_MCP_URL"] = "http://127.0.0.1:1/mcp/token"
        self.assertEqual((await self.hint())["reply"], texts.NO_MODELS["en"])

    def test_tags_map_to_supported_languages(self):
        self.assertEqual(locale.choose(["zh-HK"]), "zh-Hant")
        self.assertEqual(locale.choose(["zh-Hant-TW"]), "zh-Hant")
        self.assertEqual(locale.choose(["zh-CN"]), "zh-Hans")
        self.assertEqual(locale.choose(["zh"]), "zh-Hans")
        self.assertEqual(locale.choose(["en-GB"]), "en")
        self.assertEqual(locale.choose(["fr", "ja"]), "ja")
        self.assertEqual(locale.choose([]), "en")


if __name__ == "__main__":
    unittest.main()

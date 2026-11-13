"""在固定版本 Hermes 中加载真实插件和 gateway hook，验证最终语言包解析与并发隔离。"""

import asyncio
import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from string import Formatter
from unittest import mock

from agent import i18n, secret_scope
from gateway import hooks
from hermes_cli.plugins import PluginManager, PluginManifest
from hermes_constants import pin_process_hermes_home, reset_hermes_home_override, set_hermes_home_override
import hermes_yaml as yaml

ROOT = Path(__file__).resolve().parents[2]
KEYS = {
    "connection_unknown", "unreachable", "connection_interrupted", "auth_failed", "provider_kept_failing",
    "rate_limited", "usage_limit_resets", "bad_request", "no_credentials", "hint_auth", "hint_quota",
    "hint_overloaded", "hint_rate_limited", "hint_usage_limit_resets", "hint_usage_limit", "hint_rejected",
    "generic_failed_with_hint",
}


class ErrorLocaleCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        shutil.copytree(ROOT / "wonton", self.home / "plugins/wonton")
        shutil.copytree(ROOT / "hooks", self.home / "hooks")
        self.home.joinpath("config.yaml").write_text("plugins:\n  enabled: [wonton]\n", encoding="utf-8")
        environment = mock.patch.dict(os.environ, {"HERMES_HOME": str(self.home), "HERMES_LANGUAGE": ""})
        environment.start()
        self.addCleanup(environment.stop)
        pin_process_hermes_home(self.home)
        self.addCleanup(pin_process_hermes_home, None)
        token = set_hermes_home_override(str(self.home))
        self.addCleanup(reset_hermes_home_override, token)
        scope = secret_scope.set_secret_scope(None)
        self.addCleanup(secret_scope.reset_secret_scope, scope)
        i18n.reset_language_cache()
        self.addCleanup(i18n.reset_language_cache)
        self.manager = PluginManager(scope_key=str(self.home))
        self.addCleanup(self.manager.unload)
        manifest = PluginManifest(name="wonton", source="user", path=str(self.home / "plugins/wonton"))
        self.manager._load_plugin(manifest)
        loaded = self.manager._plugins["wonton"]
        self.assertIsNone(loaded.error)
        self.registry = hooks.HookRegistry()
        with mock.patch.object(hooks, "HOOKS_DIR", self.home / "hooks"):
            self.registry.discover_and_load()
        handler_path = self.home / "hooks/wonton-language/handler.py"
        spec = importlib.util.spec_from_file_location("test_language_handler", handler_path)
        self.handler = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.handler)

    def test_registered_keys_in_each_language_and_default_english(self):
        for lang, path in (("en", "en"), ("zh-Hans", "zh"), ("zh-Hant", "zh-hant"), ("ja", "ja")):
            catalog = yaml.safe_load((ROOT / f"wonton/locales/{path}.yaml").read_text())["gateway"]["errors"]
            self.assertEqual(set(catalog), KEYS)
            for key in KEYS:
                with self.subTest(lang=lang, key=key):
                    fields = {name: f"value-{name}" for _, name, _, _ in Formatter().parse(catalog[key]) if name}
                    rendered = i18n.t(f"gateway.errors.{key}", lang=lang, **fields)
                    self.assertEqual(rendered, catalog[key].format(**fields))
                    self.assertTrue(rendered.strip())
                    for command in ("hermes doctor", "/retry", "hermes fallback add", "hermes logs", "/login"):
                        self.assertNotIn(command, rendered)
                    if lang == "en":
                        self.assertEqual(i18n.t(f"gateway.errors.{key}", **fields), rendered)
        self.assertNotEqual(i18n.t("gateway.errors.auth_failed"), i18n.t("gateway.errors.rate_limited"))
        self.assertNotEqual(i18n.t("gateway.errors.connection_unknown"), i18n.t("gateway.errors.connection_interrupted"))

    async def test_gateway_event_uses_person_language_without_changing_credentials(self):
        user = "@person:example.org"
        scope = secret_scope.set_secret_scope({"MODEL_KEY": "scoped-value"})
        self.addCleanup(secret_scope.reset_secret_scope, scope)
        with mock.patch("httpx.AsyncClient", side_effect=AssertionError("cached language must not need network")):
            # 官方 loader 独立加载 handler，给它的现有语言缓存填入核心已知的偏好。
            handle = self.registry._resolve_handlers("agent:start")[0]
            handle.__globals__["_locale"]._cache[user] = (float("inf"), ["zh-TW"])
            await self.registry.emit("agent:start", {"platform": "matrix", "user_id": user})
        self.assertEqual(i18n.get_language(), "zh-hant")
        self.assertEqual(secret_scope.get_secret("MODEL_KEY"), "scoped-value")
        self.assertEqual(os.environ["HERMES_LANGUAGE"], "")
        self.assertNotIn("language", self.home.joinpath("config.yaml").read_text())
        self.assertEqual(
            i18n.t("gateway.errors.connection_unknown"),
            i18n.t("gateway.errors.connection_unknown", lang="zh-Hant"),
        )

    async def test_single_profile_environment_credentials_still_resolve(self):
        self.handler._locale._cache[""] = (float("inf"), ["ja"])
        with mock.patch.dict(os.environ, {"MODEL_KEY": "environment-value"}):
            await self.handler.handle("agent:start", {"platform": "telegram", "user_id": "123"})
            self.assertEqual(i18n.get_language(), "ja")
            self.assertEqual(secret_scope.get_secret("MODEL_KEY"), "environment-value")

    async def test_concurrent_people_do_not_share_language_or_scope(self):
        users = {"@one:example.org": ["ja"], "@two:example.org": ["zh-CN"]}
        self.handler._locale._cache.update({user: (float("inf"), tags) for user, tags in users.items()})
        ready = asyncio.Barrier(2)

        async def reply(user, expected):
            await self.handler.handle("agent:start", {"platform": "matrix", "user_id": user})
            await ready.wait()
            actual = await asyncio.to_thread(i18n.t, "gateway.errors.connection_unknown")
            self.assertEqual(actual, i18n.t("gateway.errors.connection_unknown", lang=expected))

        await asyncio.gather(reply("@one:example.org", "ja"), reply("@two:example.org", "zh-Hans"))
        self.assertEqual(i18n.get_language(), "en")
        self.assertIsNone(secret_scope.current_secret_scope())


if __name__ == "__main__":
    unittest.main()

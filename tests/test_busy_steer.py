"""init_config.py 的补配置行为：旧包写的 busy_input_mode=queue 在 Bot 重建容器时换成包里的取值；
主人自己改过的取不动。"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))

from init_config import apply  # noqa: E402


class BusySteerCase(unittest.TestCase):
    def test_old_pack_queue_flips_to_pack_value(self):
        config = {"display": {"busy_input_mode": "queue", "busy_ack_enabled": False}}
        seed = {"display": {"busy_input_mode": "steer"}}
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(apply(config, seed))
        self.assertEqual(config["display"]["busy_input_mode"], "steer")
        self.assertEqual(config["display"]["busy_ack_enabled"], False)

    def test_owner_set_modes_untouched(self):
        for mode in ("interrupt", "steer"):
            config = {"display": {"busy_input_mode": mode}}
            seed = {"display": {"busy_input_mode": "steer"}}
            with mock.patch.dict(os.environ, {}, clear=True):
                changed = apply(config, seed)
            self.assertEqual(config["display"]["busy_input_mode"], mode)

    def test_missing_key_and_missing_seed_untouched(self):
        # apply 同时会补 trust_env 这类注入值，这里只看 busy_input_mode 动没动。
        config = {"display": {"busy_ack_enabled": False}}
        with mock.patch.dict(os.environ, {}, clear=True):
            apply(config, {"display": {"busy_input_mode": "steer"}})
        self.assertNotIn("busy_input_mode", config["display"])
        config = {"display": {"busy_input_mode": "queue"}}
        with mock.patch.dict(os.environ, {}, clear=True):
            apply(config, {})
        self.assertEqual(config["display"]["busy_input_mode"], "queue")

    def test_interim_messages_flip_and_owner_value_untouched(self):
        config = {"display": {"platforms": {"matrix": {"interim_assistant_messages": True,
                                                       "show_reasoning": False}}}}
        seed = {"display": {"platforms": {"matrix": {"interim_assistant_messages": False}}}}
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(apply(config, seed))
        matrix = config["display"]["platforms"]["matrix"]
        self.assertIs(matrix["interim_assistant_messages"], False)
        self.assertIs(matrix["show_reasoning"], False)
        # 主人自己改过的（含关成 false 的）不动。
        config = {"display": {"platforms": {"matrix": {"interim_assistant_messages": False}}}}
        with mock.patch.dict(os.environ, {}, clear=True):
            apply(config, seed)
        self.assertIs(config["display"]["platforms"]["matrix"]["interim_assistant_messages"], False)

    def test_trust_env_still_fixed_alongside(self):
        config = {"gateway": {}, "display": {"busy_input_mode": "queue"}}
        seed = {"display": {"busy_input_mode": "steer"}}
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(apply(config, seed))
        self.assertEqual(config["gateway"]["trust_env"], False)
        self.assertEqual(config["display"]["busy_input_mode"], "steer")


if __name__ == "__main__":
    unittest.main()

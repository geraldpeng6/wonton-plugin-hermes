"""收齐附件组的行为测试：给定的钩子调用序列（带标记的事件按乱序、重复、缺条到达），
断言收住（skip）、到齐才派一条、缺条超时按已到的开一轮并提示。事件、适配器都是假的；
语言固定为中文，超时用短值，不真等 120 秒。"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))

from wonton import message_batch, texts  # noqa: E402

OWNER = "@owner:example.org"
ROOM = "!dm1:example.org"
GID = "3f0c9a1e5b7d42c6a8e1f0b2c4d6e8a0"


class Source:
    def __init__(self, room=ROOM, user_id=OWNER):
        self.chat_id, self.user_id, self.platform = room, user_id, "matrix"


class Event:
    def __init__(self, text="", media=None, marker=None, event_id="$ev"):
        self.text = text
        self.source = Source()
        self.message_id = event_id
        self.media_urls = list(media or [])
        self.media_types = ["image/png"] * len(self.media_urls)
        self.raw_message = {"msgtype": "m.text", "body": text}
        if marker is not None:
            self.raw_message["app.wonton.batch"] = marker

    def marker(self, index, count, gid=GID):
        self.raw_message["app.wonton.batch"] = {"v": 1, "id": gid, "index": index, "count": count}
        return self


class FakeAdapter:
    def __init__(self):
        self.sent = []
        self.turns = []

    async def _send_room_message(self, room, content):
        self.sent.append((room, content))
        return f"$out{len(self.sent)}"

    async def handle_message(self, event):
        self.turns.append(event)


async def zh_language(_user_id=None):
    return "zh-Hans"


class Case(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.adapter = FakeAdapter()
        for key, value in (("loop", None), ("adapter", self.adapter), ("groups", {}),
                           ("seen", {}), ("inflight", set())):
            message_batch._S[key] = value
        patcher = mock.patch.object(message_batch, "language", zh_language)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        message_batch._S["loop"] = asyncio.get_running_loop()
        await message_batch._drain()

    async def send(self, event):
        """过一遍钩子；loop 要在钩子外设好（真网关里钩子跑在事件循环上）。"""
        message_batch._S["loop"] = asyncio.get_running_loop()
        return message_batch.pre_gateway_dispatch(event=event, gateway=None)

    async def test_complete_group_is_one_turn_in_index_order(self):
        # 乱序到达：文字（最后一条）先到，两张照片后到；齐了才派发，附件按 index 排。
        text = Event("哪张好看", marker={"v": 1, "id": GID, "index": 2, "count": 3}, event_id="$ev3")
        photo0 = Event(media=["/tmp/a.png"], marker={"v": 1, "id": GID, "index": 0, "count": 3}, event_id="$ev1")
        photo1 = Event(media=["/tmp/b.png"], marker={"v": 1, "id": GID, "index": 1, "count": 3}, event_id="$ev2")
        for event in (text, photo0, photo1):
            self.assertEqual(await self.send(event), message_batch._SKIP)
        self.assertEqual(self.adapter.turns, [])
        await message_batch._drain()
        self.assertEqual(len(self.adapter.turns), 1)
        combined = self.adapter.turns[0]
        self.assertEqual(combined.media_urls, ["/tmp/a.png", "/tmp/b.png"])
        self.assertEqual(combined.media_types, ["image/png", "image/png"])
        self.assertEqual(combined.text, "哪张好看")
        self.assertNotIn("app.wonton.batch", combined.raw_message)
        # 回复锚和事件 id 都用最后一条（带文字的那条）。
        self.assertEqual(combined.message_id, "$ev3")

    async def test_duplicate_delivery_kept_once(self):
        # 同 index 的重复投递（含重投同一条、重投另一条）只收第一条。
        first = Event(media=["/tmp/a.png"], marker={"v": 1, "id": GID, "index": 0, "count": 2}, event_id="$ev1")
        again = Event(media=["/tmp/a.png"], marker={"v": 1, "id": GID, "index": 0, "count": 2}, event_id="$ev1")
        other = Event(media=["/tmp/a2.png"], marker={"v": 1, "id": GID, "index": 0, "count": 2}, event_id="$ev9")
        self.assertEqual(await self.send(first), message_batch._SKIP)
        self.assertEqual(await self.send(again), message_batch._SKIP)
        self.assertEqual(await self.send(other), message_batch._SKIP)
        text = Event("一句话", marker={"v": 1, "id": GID, "index": 1, "count": 2}, event_id="$ev2")
        self.assertEqual(await self.send(text), message_batch._SKIP)
        await message_batch._drain()
        self.assertEqual(len(self.adapter.turns), 1)
        self.assertEqual(self.adapter.turns[0].media_urls, ["/tmp/a.png"])

    async def test_incomplete_group_opens_partial_after_timeout(self):
        with mock.patch.object(message_batch, "TIMEOUT_SECONDS", 0.05):
            photo = Event(media=["/tmp/a.png"], marker={"v": 1, "id": GID, "index": 0, "count": 3}, event_id="$ev1")
            self.assertEqual(await self.send(photo), message_batch._SKIP)
            self.assertEqual(self.adapter.turns, [])
            await asyncio.sleep(0.2)
        await message_batch._drain()
        self.assertEqual(len(self.adapter.turns), 1)
        combined = self.adapter.turns[0]
        self.assertEqual(combined.text, texts.BATCH_PARTIAL["zh-Hans"].format(total=3, received=1))
        self.assertEqual(combined.media_urls, ["/tmp/a.png"])
        # 房间里另发一条 m.notice 告诉主人。
        self.assertEqual(self.adapter.sent, [(ROOM, {"msgtype": "m.notice",
                                                   "body": texts.BATCH_NOTICE["zh-Hans"].format(total=3, received=1)})])

    async def test_timeout_counts_from_last_arrival(self):
        # 计时从这组最后到的一条算起：中途到的会重置计时。
        with mock.patch.object(message_batch, "TIMEOUT_SECONDS", 0.1):
            first = Event(media=["/tmp/a.png"], marker={"v": 1, "id": GID, "index": 0, "count": 3}, event_id="$ev1")
            self.assertEqual(await self.send(first), message_batch._SKIP)
            await asyncio.sleep(0.06)
            second = Event(media=["/tmp/b.png"], marker={"v": 1, "id": GID, "index": 1, "count": 3}, event_id="$ev2")
            self.assertEqual(await self.send(second), message_batch._SKIP)
            await asyncio.sleep(0.06)
            self.assertEqual(self.adapter.turns, [])  # 最初那条的宽限已过，但计时被重置
            await asyncio.sleep(0.1)
        await message_batch._drain()
        self.assertEqual(len(self.adapter.turns), 1)
        self.assertEqual(self.adapter.turns[0].text,
                         texts.BATCH_PARTIAL["zh-Hans"].format(total=3, received=2))

    async def test_late_event_after_partial_opens_alone(self):
        with mock.patch.object(message_batch, "TIMEOUT_SECONDS", 0.05):
            photo = Event(media=["/tmp/a.png"], marker={"v": 1, "id": GID, "index": 0, "count": 2}, event_id="$ev1")
            self.assertEqual(await self.send(photo), message_batch._SKIP)
            await asyncio.sleep(0.2)
        await message_batch._drain()
        # 开过轮的组：一小时内的同 id 事件按单独一条放行（不收住）。
        late = Event(media=["/tmp/b.png"], marker={"v": 1, "id": GID, "index": 1, "count": 2}, event_id="$ev2")
        self.assertIsNone(await self.send(late))
        await message_batch._drain()
        self.assertEqual([event.message_id for event in self.adapter.turns], ["$ev1"])

    async def test_seen_id_forgets_after_an_hour(self):
        with mock.patch.object(message_batch, "TIMEOUT_SECONDS", 0.05):
            photo = Event(media=["/tmp/a.png"], marker={"v": 1, "id": GID, "index": 0, "count": 2}, event_id="$ev1")
            self.assertEqual(await self.send(photo), message_batch._SKIP)
            await asyncio.sleep(0.2)
        await message_batch._drain()
        self.assertEqual(message_batch._S["groups"], {})
        message_batch._S["seen"][GID] -= message_batch.SEEN_TTL_SECONDS + 1
        with mock.patch.object(message_batch, "TIMEOUT_SECONDS", 0.05):
            again = Event("又一句话", marker={"v": 1, "id": GID, "index": 1, "count": 2}, event_id="$ev2")
            # 一小时后同 id 重新当一组收：先收住，没到齐不开轮。
            self.assertEqual(await self.send(again), message_batch._SKIP)
            self.assertEqual(len(message_batch._S["groups"]), 1)
            await asyncio.sleep(0.2)
        await message_batch._drain()
        self.assertEqual(message_batch._S["groups"], {})

    async def test_single_event_group_dispatches_at_once(self):
        # 带标记但只有一条（比如文件列表）：不用等，马上开轮。
        files = Event("这是上个月的账单\n\nwonton://file/abcdefghij01",
                      marker={"v": 1, "id": GID, "index": 0, "count": 1}, event_id="$ev1")
        self.assertEqual(await self.send(files), message_batch._SKIP)
        await message_batch._drain()
        self.assertEqual(len(self.adapter.turns), 1)
        self.assertEqual(self.adapter.turns[0].text, files.text)
        self.assertNotIn("app.wonton.batch", self.adapter.turns[0].raw_message)

    async def test_invalid_markers_pass_through(self):
        cases = [
            {"v": 2, "id": GID, "index": 0, "count": 2},           # 不认识的版本
            {"v": 1, "id": "XYZ", "index": 0, "count": 2},         # id 不是 32 位小写十六进制
            {"v": 1, "id": GID.upper(), "index": 0, "count": 2},
            {"v": 1, "id": GID, "index": 2, "count": 2},           # index 不小于 count
            {"v": 1, "id": GID, "index": -1, "count": 2},
            {"v": 1, "id": GID, "index": 0, "count": 0},           # count 超范围
            {"v": 1, "id": GID, "index": 0, "count": 33},
            {"v": 1, "id": GID, "index": 0.0, "count": 2},         # 不是整数
            {"v": 1, "id": GID, "index": True, "count": 2},
            {"v": 1, "index": 0, "count": 2},                      # 缺 id
            "not a dict",
        ]
        for marker in cases:
            event = Event("一句话", marker=marker, event_id="$ev1")
            self.assertIsNone(await self.send(event), marker)
        await message_batch._drain()
        self.assertEqual(self.adapter.turns, [])
        self.assertEqual(message_batch._S["groups"], {})

    async def test_unmarked_and_plain_events_pass_through(self):
        self.assertIsNone(await self.send(Event("一句话", event_id="$ev1")))
        self.assertIsNone(await self.send(Event("无内容标记", event_id="$ev2")))
        bare = Event("有别的顶层键", event_id="$ev3")
        bare.raw_message["app.wonton.side_chat"] = {"v": 1}
        self.assertIsNone(await self.send(bare))
        await message_batch._drain()
        self.assertEqual(self.adapter.turns, [])

    async def test_missing_adapter_lets_messages_through(self):
        # 适配器不在（网关还没就绪）时不收住：宁可一条一轮，不吞消息。
        message_batch._S["adapter"] = None
        event = Event("一句话", marker={"v": 1, "id": GID, "index": 0, "count": 2}, event_id="$ev1")
        self.assertIsNone(await self.send(event))

    async def test_busy_group_still_one_call(self):
        # 忙时整组是一个整体：插话/排队由网关按一条消息处理，这里只保证只交一条。
        text = Event("哪张好看", marker={"v": 1, "id": GID, "index": 2, "count": 3}, event_id="$ev3")
        photo0 = Event(media=["/tmp/a.png"], marker={"v": 1, "id": GID, "index": 0, "count": 3}, event_id="$ev1")
        photo1 = Event(media=["/tmp/b.png"], marker={"v": 1, "id": GID, "index": 1, "count": 3}, event_id="$ev2")
        for event in (photo0, text, photo1):
            self.assertEqual(await self.send(event), message_batch._SKIP)
        await message_batch._drain()
        self.assertEqual(len(self.adapter.turns), 1)
        self.assertEqual(len(self.adapter.turns[0].media_urls), 2)


if __name__ == "__main__":
    unittest.main()

"""旁聊行为测试：给定钩子调用序列（话题会话一轮结束 / 失败 / 重置 / 主会话下一轮），
断言发出的结果行和状态事件、注入的上下文。事件 JSON 按 ADR-0093 §1 的样例做契约断言。

Matrix 客户端和会话库都是假的；语言固定为中文，避免真的去问核心。
"""

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))

from wonton import side_chat, texts  # noqa: E402

OWNER = "@owner:example.org"
BOT = "@bot:example.org"
ROOM = "!dm1:example.org"
ROOT = "$root1"
MAIN_SID = "main-session"
THREAD_SID = "thread-session"


class Origin:
    def __init__(self, room, thread=None):
        self.chat_id, self.thread_id, self.platform = room, thread, "matrix"


class Entry:
    def __init__(self, origin):
        self.origin = origin


class Store:
    """会话库：只实现插件用到的三个方法。"""

    def __init__(self):
        self.entries, self.keys = {}, {}

    def add(self, sid, origin, key):
        self.entries[sid] = Entry(origin)
        self.keys[key] = sid

    def lookup_by_session_id(self, sid):
        return self.entries.get(sid)

    def _generate_session_key(self, source):
        return f"key:{source.chat_id}:{source.thread_id or ''}"

    def peek_session_id(self, key):
        return self.keys.get(key)


class Source:
    def __init__(self, room, thread=None, chat_type="dm", user_id=OWNER):
        self.chat_id, self.thread_id, self.chat_type = room, thread, chat_type
        self.user_id, self.platform = user_id, "matrix"


class Event:
    def __init__(self, text, source):
        self.text, self.source = text, source


class Members:
    def __init__(self, members):
        self.members = members


class FakeClient:
    def __init__(self):
        self.state_events = []
        self.roots = {}
        self.members = {}

    async def send_state_event(self, room, event_type, content, state_key=""):
        self.state_events.append((room, event_type, content, state_key))
        return "$st1"

    async def get_event(self, room, event_id):
        if event_id not in self.roots:
            raise RuntimeError(f"event {event_id} not found")
        return self.roots[event_id]

    async def joined_members(self, room):
        if room not in self.members:
            raise RuntimeError(f"no members for {room}")
        return self.members[room]


class RootEvent:
    """话题根：content 是 dict（未加密的假事件）。"""

    type = "m.room.message"

    def __init__(self, content):
        self.content = content


class FakeAdapter:
    def __init__(self, client):
        self._client = client
        self._joined_rooms = set()
        self.sent = []

    async def _send_room_message(self, room, content):
        self.sent.append((room, content))
        return f"$ev{len(self.sent)}"


async def zh_language(_user_id=None):
    return "zh-Hans"


class Case(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = FakeClient()
        self.adapter = FakeAdapter(self.client)
        self.store = Store()
        store = self.store
        # 主会话无 thread_id；话题会话挂着话题根。
        store.add(MAIN_SID, Origin(ROOM), "key:%s:" % ROOM)
        store.add(THREAD_SID, Origin(ROOM, ROOT), "key:%s:%s" % (ROOM, ROOT))
        for key, value in (("loop", None), ("adapter", None), ("session_store", None),
                           ("sessions", {}), ("threads", {}), ("senders", {}),
                           ("declared", set()), ("enumerated", False), ("inflight", set())):
            side_chat._S[key] = value
        side_chat._S["session_store"] = self.store
        self.patches = [
            mock.patch.dict(os.environ, {"MATRIX_USER_ID": BOT}),
            mock.patch.object(side_chat, "language", zh_language),
            mock.patch.object(side_chat, "_adapter", lambda: self.adapter),
        ]
        for patch in self.patches:
            patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in self.patches])

    async def asyncTearDown(self):
        await self.drain()

    async def dispatch(self, text, thread=None, chat_type="dm"):
        source = Source(ROOM, thread, chat_type)
        return side_chat.pre_gateway_dispatch(event=Event(text, source),
                                              gateway=None, session_store=self.store)

    async def drain(self):
        await side_chat._drain()

    def sent(self):
        return [content for _, content in self.adapter.sent]

    def result_lines(self):
        return [c for c in self.sent() if "app.wonton.side_chat_result" in c]

    async def test_done_result_line_matches_adr_sample(self):
        await self.dispatch("整理一下手机里的照片", thread=ROOT)
        side_chat.post_llm_call(session_id=THREAD_SID,
                                assistant_response="我把照片按日期分好了。\n\n第二段不要出现。")
        side_chat.on_session_end(session_id=THREAD_SID, completed=True)
        await self.drain()
        (content,) = self.result_lines()
        self.assertEqual(content["msgtype"], "m.notice")
        self.assertEqual(content["m.relates_to"], {"m.in_reply_to": {"event_id": ROOT}})
        # ADR-0093 §1 的契约字段：字段名、v、root、state。
        self.assertEqual(content["app.wonton.side_chat_result"],
                         {"v": 1, "root": ROOT, "title": "整理一下手机里的照片", "state": "done"})
        self.assertEqual(content["body"], "旁聊「整理一下手机里的照片」：已完成")

    async def test_title_from_root_marked_field(self):
        self.client.roots[ROOT] = RootEvent({
            "msgtype": "m.notice", "body": "App 写进根的标题",
            "app.wonton.side_chat": {"v": 1, "title": "根上的标题"}})
        await self.dispatch("整理一下手机里的照片", thread=ROOT)
        side_chat.on_session_end(session_id=THREAD_SID, completed=True)
        await self.drain()
        field = self.result_lines()[0]["app.wonton.side_chat_result"]
        self.assertEqual(field["title"], "根上的标题")

    async def test_title_from_root_body_first_line_clipped(self):
        self.client.roots[ROOT] = RootEvent({"msgtype": "m.notice", "body": "第一行的标题很长很长很长很长\n第二行"})
        await self.dispatch("别的第一句", thread=ROOT)
        side_chat.on_session_end(session_id=THREAD_SID, completed=True)
        await self.drain()
        field = self.result_lines()[0]["app.wonton.side_chat_result"]
        self.assertEqual(field["title"], "第一行的标题很长很长很长很长"[:20])

    async def test_title_falls_back_to_ingress_first_line(self):
        await self.dispatch("进话题的第一句第一行\n第二行", thread=ROOT)  # 根取不到
        side_chat.on_session_end(session_id=THREAD_SID, completed=True)
        await self.drain()
        field = self.result_lines()[0]["app.wonton.side_chat_result"]
        self.assertEqual(field["title"], "进话题的第一句第一行"[:20])

    async def test_failed_and_interrupted_send_failed(self):
        await self.dispatch("问题", thread=ROOT)
        side_chat.on_session_end(session_id=THREAD_SID, completed=False, failed=True, interrupted=False)
        side_chat.on_session_end(session_id=THREAD_SID, completed=False, failed=False, interrupted=True)
        await self.drain()
        states = [c["app.wonton.side_chat_result"]["state"] for c in self.result_lines()]
        self.assertEqual(states, ["failed", "failed"])

    async def test_main_session_turns_send_nothing(self):
        await self.dispatch("主对话的一句话")
        side_chat.post_llm_call(session_id=MAIN_SID, assistant_response="主对话的回答")
        side_chat.on_session_end(session_id=MAIN_SID, completed=True)
        side_chat.on_session_reset(session_id=MAIN_SID, old_session_id=MAIN_SID)
        await self.drain()
        self.assertEqual(self.sent(), [])

    async def test_non_matrix_sessions_send_nothing(self):
        self.store.add("tg-sid", Origin("!tg:example.org", "$tgroot"), "tg-key")
        side_chat.on_session_end(session_id="tg-sid", completed=True)
        side_chat.on_session_reset(session_id=None, old_session_id="tg-sid")
        await self.drain()
        self.assertEqual(self.sent(), [])

    async def test_reset_sends_ended_after_confirmation(self):
        await self.dispatch("话题里的问题", thread=ROOT)
        side_chat.post_llm_call(session_id=THREAD_SID, assistant_response="答好了")
        side_chat.on_session_end(session_id=THREAD_SID, completed=True)  # 第一轮 done
        await self.drain()
        # 话题里 /new，主人回 !approve 后上游才发 on_session_reset。
        side_chat.on_session_reset(session_id="new-session", old_session_id=THREAD_SID)
        await self.drain()
        states = [c["app.wonton.side_chat_result"]["state"] for c in self.result_lines()]
        self.assertEqual(states, ["done", "ended"])
        self.assertEqual(self.result_lines()[1]["body"], "旁聊「话题里的问题」：已结束")

    async def test_injection_lists_new_results_once(self):
        await self.dispatch("整理一下手机里的照片", thread=ROOT)
        side_chat.post_llm_call(session_id=THREAD_SID, assistant_response="我把照片按日期分好了。\n\n第二段不要出现。")
        side_chat.on_session_end(session_id=THREAD_SID, completed=True)
        context = await side_chat.pre_llm_call(session_id=MAIN_SID)
        self.assertEqual(context["context"].splitlines()[0], "旁聊的动态：")
        self.assertIn("- 旁聊「整理一下手机里的照片」已完成：我把照片按日期分好了。", context["context"])
        self.assertNotIn("第二段", context["context"])
        # 注入过一次就不再重复，直到有新结果。
        self.assertIsNone(await side_chat.pre_llm_call(session_id=MAIN_SID))

    async def test_injection_brief_when_no_excerpt(self):
        await self.dispatch("话题里的问题", thread=ROOT)
        side_chat.on_session_reset(session_id="new-session", old_session_id=THREAD_SID)
        context = await side_chat.pre_llm_call(session_id=MAIN_SID)
        self.assertIn("- 旁聊「话题里的问题」已结束", context["context"])

    async def test_injection_none_for_thread_session_and_empty_main(self):
        await self.dispatch("话题里的问题", thread=ROOT)
        self.assertIsNone(await side_chat.pre_llm_call(session_id=THREAD_SID))
        self.assertIsNone(await side_chat.pre_llm_call(session_id=MAIN_SID))

    async def test_excerpt_clipped_to_200_chars(self):
        await self.dispatch("问题", thread=ROOT)
        side_chat.post_llm_call(session_id=THREAD_SID, assistant_response="长" * 300)
        side_chat.on_session_end(session_id=THREAD_SID, completed=True)
        context = await side_chat.pre_llm_call(session_id=MAIN_SID)
        excerpt = "长" * 200 + "…"
        self.assertIn(excerpt, context["context"])
        self.assertNotIn("长" * 201, context["context"])

    async def test_declaration_written_in_dm_with_bot_state_key(self):
        await self.dispatch("你好")
        await self.drain()
        self.assertEqual(self.client.state_events, [(ROOM, side_chat.DECLARATION, {"v": 1}, BOT)])

    async def test_declaration_once_per_room(self):
        await self.dispatch("你好")
        await self.dispatch("又一句")
        await self.drain()
        self.assertEqual(len(self.client.state_events), 1)

    async def test_declaration_enumerates_dm_rooms_by_member_count(self):
        self.adapter._joined_rooms = {ROOM, "!group:example.org"}
        self.client.members = {ROOM: Members([OWNER, BOT]),
                               "!group:example.org": Members([OWNER, BOT, "@c:example.org"])}
        await self.dispatch("群房里的一句", chat_type="group")
        await self.drain()
        self.assertEqual([event[0] for event in self.client.state_events], [ROOM])
        self.assertEqual(self.client.state_events[0][3], BOT)

    async def test_dispatch_fills_session_mapping_for_reset_after_restart(self):
        # 新会话的第一条消息进来时库里查不到它；发过结果后 reset 要能按记下的对应关系找到房间和话题。
        self.store.keys.clear()  # 模拟 peek 查不到（会话在那条消息之后才建）
        await self.dispatch("话题里的问题", thread=ROOT)
        self.store.add(THREAD_SID, Origin(ROOM, ROOT), "late-key")
        side_chat.on_session_end(session_id=THREAD_SID, completed=True)
        await self.drain()
        self.assertEqual(len(self.result_lines()), 1)
        side_chat.on_session_reset(session_id="newer-session", old_session_id=THREAD_SID)
        await self.drain()
        self.assertEqual([c["app.wonton.side_chat_result"]["state"] for c in self.result_lines()],
                         ["done", "ended"])

    async def test_send_failure_only_logs(self):
        async def boom(room, content):
            raise RuntimeError("homeserver unreachable")
        self.adapter._send_room_message = boom
        await self.dispatch("问题", thread=ROOT)
        side_chat.on_session_end(session_id=THREAD_SID, completed=True)
        await self.drain()  # 不抛出、不重试
        self.assertEqual(self.result_lines(), [])
        # 发送失败不影响下一轮注入：结果已经记下。
        context = await side_chat.pre_llm_call(session_id=MAIN_SID)
        self.assertIn("旁聊的动态：", context["context"])


if __name__ == "__main__":
    unittest.main()

"""给主人的提示和旁聊发回主时间线的文字，四种语言各一条。

入口名照 App 里实际显示的写：「应用 → 模型设置」和「Apps → Models」。模型设置的清单只有中文和英文，
繁中界面显示简体标题，日文界面显示英文标题，所以这两条这样写；和 App 的清单一起改。

旁聊的引号沿用 ACP 桥的话题文字（四种语言都用「」，见 wonton-acp-bridge 的 locales/*/topics.ftl）。
"""

NO_MODELS = {
    "en": "No model is available yet: add a model provider and its API key in Apps → Models, then message me again.",
    "zh-Hans": "还没有可用的模型：请在「应用 → 模型设置」里添加一个模型服务商和它的 API key，再给我发消息。",
    "zh-Hant": "還沒有可用的模型：請在「應用程式 → 模型设置」裡新增模型供應商和它的 API 金鑰，再傳訊息給我。",
    "ja": "利用できるモデルがまだありません。アプリ → Models でモデルプロバイダーと API キーを追加してから、もう一度送ってください。",
}

NO_DEFAULT = {
    "en": "The default model {model} has no usable model: check the routing group {group} in Apps → Models.",
    "zh-Hans": "默认模型 {model} 里没有可用的模型：请在「应用 → 模型设置」里检查名为 {group} 的路由组。",
    "zh-Hant": "預設模型 {model} 裡沒有可用的模型：請在「應用程式 → 模型设置」裡檢查名為 {group} 的路由群組。",
    "ja": "既定のモデル {model} に使えるモデルがありません。アプリ → Models で、ルーティンググループ {group} を確認してください。",
}

# 旁聊结果行的 body（ADR-0093 §1 的样例：旁聊「标题」：已完成）。state 用英文码进结构化字段，
# body 里的说法用主人的语言。
SIDE_CHAT_BODY = {
    "en": "Side chat 「{title}」: {state}",
    "zh-Hans": "旁聊「{title}」：{state}",
    "zh-Hant": "旁聊「{title}」：{state}",
    "ja": "サイドチャット「{title}」：{state}",
}

SIDE_CHAT_STATE = {
    "done": {"en": "done", "zh-Hans": "已完成", "zh-Hant": "已完成", "ja": "完了"},
    "failed": {"en": "failed", "zh-Hans": "失败", "zh-Hant": "失敗", "ja": "失敗"},
    "ended": {"en": "ended", "zh-Hans": "已结束", "zh-Hant": "已結束", "ja": "終了"},
}

# 主会话下一轮注入的旁聊动态，格式照 ACP 桥的话题动态（标题行 + 「- 」条目）。
SIDE_CHAT_HEADER = {
    "en": "Side chat updates:",
    "zh-Hans": "旁聊的动态：",
    "zh-Hant": "旁聊的動態：",
    "ja": "サイドチャットの動き:",
}

SIDE_CHAT_DIGEST = {
    "en": "Side chat 「{title}」: {state}. {excerpt}",
    "zh-Hans": "旁聊「{title}」{state}：{excerpt}",
    "zh-Hant": "旁聊「{title}」{state}：{excerpt}",
    "ja": "サイドチャット「{title}」{state}：{excerpt}",
}

# 没有摘录（Bot 重启过、或那轮没有回复）时只报标题和状态。
SIDE_CHAT_DIGEST_BRIEF = {
    "en": "Side chat 「{title}」: {state}",
    "zh-Hans": "旁聊「{title}」{state}",
    "zh-Hant": "旁聊「{title}」{state}",
    "ja": "サイドチャット「{title}」{state}",
}

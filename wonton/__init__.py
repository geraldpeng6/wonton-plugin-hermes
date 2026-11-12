"""Wonton 给 Hermes 的钩子。

模型网关没接上（注入的模型变量为空）、或网关里还没有默认模型时（主人还没在「模型设置」里添加模型服务商），
直接回一句该怎么做，不调模型。提示按说话人自己的语言写（核心按人记着 App 的语言），只有一种，不把几种语言拼在一起。

不拦的话，网关的报错会原样发进聊天：magpie 说没有这个模型的报错很长，绕过了 Hermes 对模型报错的改写。

每条消息先问一次网关有哪些模型（同一个 Docker 网络里，几毫秒）：主人加了 key，下一条消息就照常回答，不用重启。
网关连不上、回答不对时不拦，交给 Hermes 照常处理和报错，这里不猜原因。

每轮带上收发文件的办法。收文件：主人用 App 发的文件是正文里的 wonton://file/<编号>，Hermes 只看到文字；PDF 这类
wonton__view_file 读不了时，模型会让主人「作为附件再发一次」，主人照做还是一样，所以要它照实说读不了。存文件：存进共享文件夹后
模型会说成「存进了相册」，相册应用里却可能找不到，所以要它照结果说文件夹和路径。
发文件：Hermes 把工具结果里的预览存成缓存文件，写上 MEDIA:<路径>，它的 Matrix 提示
又教模型用 MEDIA: 发文件，没有这句话时模型很可能把预览发给主人。存文件的办法也在这里：outbox 的路径是 Hermes 的挂载决定的。
同一轮还带一句引导语：主人想要新页面或应用时，先调 wonton__plugin_guide。
应用的事（记笔记、存文件等）用应用经总线提供的工具（tool_search 目录的「wonton tools」组），身份云吞已带好，
不让用户生成令牌，调不通就照实说、不改存本地。上游把 MCP 工具收进 tool_search 延迟目录，模型绕过桥直接调会被
「工具不存在」拒掉，之后就自己找地方存、让用户去网页生成令牌——所以引导要每轮都在，不能只指望技能。
工具错误和确认过程也要按结果回答：调用前的预告不是执行结果，返回速度和成功结果都不能证明没有经过确认卡片。

旁聊（私聊里的 Matrix 话题）的结果行、主会话注入和声明在 side_chat.py。
"""

import logging
import os

import httpx

from .locale import language
from .texts import NO_DEFAULT, NO_MODELS

logger = logging.getLogger(__name__)

SEND_FILES = (
    "发给主人的文件来自工具结果里的编号时（「文件 …，编号 …」）：调 wonton__share_file，把返回的 wonton://file/ 链接原样放进回复，"
    "不要改动它，也不要用 MEDIA: 发这个文件或它的预览：预览只是缩小的图。你自己生成的图片和语音照旧用 MEDIA:。"
    "主人要把文件放进音乐库这类共享文件夹时：把文件放进 /opt/data/outbox，用 wonton__list_shares 看有哪些文件夹，再调 wonton__save_file，"
    "这是唯一的办法。存好以后按结果说存进了哪个共享文件夹、什么路径（如「存进了 photos 共享文件夹」）；共享文件夹不是应用，"
    "不要说成「存进了相册」「放进了音乐库」，也不要替应用保证它会显示出来。"
)

RECEIVED_FILES = (
    "主人消息里单独一行的 wonton://file/<编号> 是主人用 App 发给你的文件，不是只发了名字：要看内容就用这个编号调 wonton__view_file。"
    "它返回 not_viewable 时，是云吞还读不了这种文件的内容：照实告诉主人收到了哪个文件、读不了这种文件的内容；"
    "不要让主人再发一次、换成附件发或者放到服务器上的某个路径，主人已经用附件发过了，重发也一样。"
)

WRITE_PLUGIN = "主人想要一个新的页面、应用或小工具，或者要改你之前写的插件时：先调 wonton__plugin_guide，照它说的做。"

TOOL_RESULTS = (
    "只按实际工具结果汇报，没调用的工具不能说已查过或执行过。调用前只预告将请求操作，不能说已开始、已完成或确认卡片已发出。"
    "Hermes 将工具失败标为顶层非空 error：遇到它，说明哪一步没成、结果给出的原因（用人话）和用户能做什么；不能继续说已开始或已完成。"
    "结果不确定就说不确定。确认卡片由核心处理，返回快或返回成功都不能证明没经过确认；"
    "结果没明确说明本次是否经过主人确认时，说不知道，不能编造没发卡片、绕过确认或主人已经点了允许。"
)

WONTON_APPS = (
    "主人要在家里装的应用里做事（记笔记、存文件、听歌这类）时，用那个应用经总线提供的工具：它们在 tool_search 目录的"
    "「wonton tools」一组里（名字形如 mcp__wonton__<应用>__…），照 tool_search 的说明先 tool_describe 拿参数、"
    "再 tool_call 调用，不能把目录里的名字直接当普通工具调。这些工具的身份由云吞带好：永远不要让用户去应用网页"
    "生成令牌、API key 或密码发给你，也不要把这类凭据写进聊天。应用没装、没就绪或调用失败时照实说哪一步没走通，"
    "不能把内容改存到本地文件、Obsidian 库或 memory 里。"
)


async def missing_model_hint(text="", source=None, **_):
    """``post_gateway_admission``：默认模型不在网关的模型列表里时，回复提示，这条消息不再交给模型。"""
    if text.lstrip().startswith("/"):
        return None
    source = source or {}
    user = source.get("user_id")
    sender = user if source.get("platform") == "matrix" and isinstance(user, str) and user.startswith("@") else None
    base = os.environ.get("WONTON_MODEL_BASE_URL", "").rstrip("/")
    key = os.environ.get("WONTON_MODEL_API_KEY", "")
    model = os.environ.get("WONTON_MODEL_DEFAULT", "")
    if not (base and key and model):
        # 模型网关没接上（变量为空，spec：“models”是“有网关就用”）：主人的模型还配在网关这个 provider 上时，
        # 直接给指引，不把 Hermes 对空地址的原始报错发进聊天；主人已经换成自己的服务商就不拦。
        if _gateway_provider_active():
            return {"action": "handled", "reply": NO_MODELS[await language(sender)]}
        return None
    try:
        # 网关在插件网络里，不走代理环境变量（Hermes 的 gateway.trust_env 也是关的）。
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            response = await client.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
            response.raise_for_status()
            ids = {item["id"] for item in response.json()["data"]}
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        logger.warning("查询模型网关的模型列表失败，照常交给 Hermes：%r", e)
        return None
    if model in ids:
        return None
    lang = await language(sender)
    if ids:
        reply = NO_DEFAULT[lang].format(model=model, group=model.removeprefix("group/"))
    else:
        reply = NO_MODELS[lang]
    return {"action": "handled", "reply": reply}


def _gateway_provider_active() -> bool:
    """主人的模型还配在网关这个 provider 上才算（配置读不出来时不拦，不误导）。"""
    try:
        from hermes_cli.config import load_config_readonly

        model_cfg = (load_config_readonly() or {}).get("model") or {}
        provider = str(model_cfg.get("provider") or "").strip()
    except Exception:
        logger.warning("读 config.yaml 里的模型 provider 失败，不当成网关处理", exc_info=True)
        return False
    return provider in ("", "magpie", "custom:magpie")


def send_files_context(**_):
    """``pre_llm_call``：返回的文字追加在这一轮的用户消息后面，不进系统提示。"""
    return {"context": f"{SEND_FILES}\n{RECEIVED_FILES}\n{WRITE_PLUGIN}\n{TOOL_RESULTS}\n{WONTON_APPS}"}


def register(ctx):
    ctx.register_hook("post_gateway_admission", missing_model_hint)
    ctx.register_hook("pre_llm_call", send_files_context)
    from . import side_chat

    side_chat.register(ctx)

#!/usr/bin/env bash
# scripts/smoke.sh 的一项一项探测，跟着插件走。由 harness source，变量由 harness 提供。
# shellcheck disable=SC2034  # DATA_NA 由 e2e/smoke.sh 读取
#
# 清单有 webui（原生 Channels 页）：网页打得开由 web_assert 验页面注入的会话 token 和回环模式；
# Hermes 的 dashboard 没有独立账号，主人身份由核心网关校验，按 IDENTITY_NA 记 NA。
#
# 数据：data 钩子只允许 SMOKE_* 变量和 web_get / dweb，都是打插件网页的通道，页面不写实例数据。
# Bot 数据目录里能留下的只有 Matrix 会话加密状态和对话历史，
# 钩子约束内（无 docker exec、无 Matrix 配对）够不到；它们的存活由 scripts/e2e-hermes.sh
# 的 agent_smoke 覆盖：配对主人隧道后，加密 /version 在网关进程重启前后都成功，说明加密
# 状态确实从数据目录重载。因此声明 DATA_NA=1，升级保数据、重装保数据按声明记 NA。
DATA_NA=1
# shellcheck disable=SC2034
# Hermes 的回环 dashboard 没有独立账号；主人身份由核心网关校验。
IDENTITY_NA=1
SMOKE_NOTE="原生 dashboard 使用页面注入的会话 token；主人会话由核心网关验证，应用身份固定为 local。加密 Matrix、模型与 MCP 专项探测见 scripts/e2e-hermes.sh。"

web_assert() {
  python3 -c 'import re,sys
page=sys.stdin.read()
assert re.search(r"window\.__HERMES_SESSION_TOKEN__=\"[^\"]+\"", page), "缺少 Hermes 原生会话 token"
assert "window.__HERMES_AUTH_REQUIRED__=false" in page, "dashboard 不是回环模式"
'
}

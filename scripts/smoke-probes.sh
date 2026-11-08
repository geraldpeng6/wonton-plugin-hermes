# shellcheck shell=bash disable=SC2034
# Hermes 的清单有 webui（默认 Bot 的原生 Channels 页，sso: none）：
#   网页打得开由 web_assert 验页面注入的会话 token 和回环模式；
#   dashboard 没有独立账号，主人身份由核心网关校验，按 IDENTITY_NA 记 N/A。
#
# 数据：data 钩子只允许 SMOKE_* 变量和 web_get / dweb，都是打插件网页的通道，页面不写实例数据。
# Bot 数据目录里能留下的只有 Matrix 会话加密状态和对话历史，钩子约束内（无 docker exec、无 Matrix 配对）
# 够不到；加密私聊和会话重载在专项验收里实测。因此声明 DATA_NA=1，升级保数据、重装保数据按声明记 N/A。
#
# 共享验收可能已卸掉预装的 Hermes；仍装着时才保留数据卸载，再由调用方安装工作区版本。
before_install() {
  local installed
  installed="$(probes_dev status)" || return 1
  if grep -q '^com\.nousresearch\.hermes[[:space:]]' <<<"$installed"; then
    probes_dev uninstall
  fi
}

DATA_NA=1
IDENTITY_NA=1
SMOKE_NOTE="原生 dashboard 使用页面注入的会话 token；主人会话由核心网关验证，应用身份固定为 local。加密 Matrix 私聊、模型回答和 Wonton MCP 在专项验收里实测。"

web_assert() {
  python3 -c 'import re,sys
page=sys.stdin.read()
assert re.search(r"window\.__HERMES_SESSION_TOKEN__=\"[^\"]+\"", page), "缺少 Hermes 原生会话 token"
assert "window.__HERMES_AUTH_REQUIRED__=false" in page, "dashboard 不是回环模式"
'
}

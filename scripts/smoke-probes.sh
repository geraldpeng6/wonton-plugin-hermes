# shellcheck shell=bash disable=SC2034
# Hermes 的清单有 webui（默认 Bot 的原生 Channels 页，sso: none）：
#   网页打得开由 web_assert 验页面注入的会话 token 和回环模式；
#   dashboard 没有独立账号：用户身份验的是核心网关放行的会话拿页面注入的 token 进到原生的回环身份。
#
# 数据：data 钩子只允许 SMOKE_* 变量和 web_get / dweb，都是打插件网页的通道，页面不写实例数据。
# Bot 数据目录里能留下的只有 Matrix 会话加密状态和对话历史，钩子约束内（无 docker exec、无 Matrix 配对）
# 够不到；加密私聊和会话重载在附加检查里实测。因此声明 DATA_NA=1，升级保数据、重装保数据按声明记 N/A。
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
SMOKE_NOTE="原生 dashboard 使用页面注入的会话 token；主人会话由核心网关验证，应用身份固定为 local。"

web_assert() {
  python3 -c 'import re,sys
page=sys.stdin.read()
assert re.search(r"window\.__HERMES_SESSION_TOKEN__=\"[^\"]+\"", page), "缺少 Hermes 原生会话 token"
assert "window.__HERMES_AUTH_REQUIRED__=false" in page, "dashboard 不是回环模式"
'
}

# 原生 dashboard 的请求：页面注入的会话 token 放在 X-Hermes-Session-Token 头里（同上游 web/src/lib/api.ts）；
# 不带 token 的请求即使有网关会话也会被拒。
hermes_token() {
  HERMES_TOKEN="$(curl_t "$SMOKE_WEB_MAX_TIME" "$SMOKE_WEB_ORIGIN/" --noproxy '*' -L -b "$SMOKE_JAR" -c "$SMOKE_JAR" \
    -H 'Accept: text/html' | sed -n 's/.*window\.__HERMES_SESSION_TOKEN__="\([^"]*\)".*/\1/p' | head -1)"
  [[ -n "$HERMES_TOKEN" ]]
}
hermes_dash() { # <方法> <路径>
  curl_t "$SMOKE_WEB_MAX_TIME" "$SMOKE_WEB_ORIGIN$2" --noproxy '*' --fail-with-body -X "$1" \
    -b "$SMOKE_JAR" -c "$SMOKE_JAR" -H "X-Hermes-Session-Token: $HERMES_TOKEN"
}

identity_check() { # 用户的网关会话拿页面注入的 token，进到原生 dashboard 的回环身份（不是另一套 Hermes 账号）
  local me
  hermes_token || { echo "页面里没有注入的会话 token"; return 1; }
  me="$(hermes_dash GET /api/auth/me)" || { echo "/api/auth/me 失败：$me"; return 1; }
  jq -e '.user_id == "local" and .provider == "loopback"' <<<"$me" >/dev/null ||
    { echo "原生身份不是回环的 local：$me"; return 1; }
}

# Agent 专项：附加检查里用套件的 Agent 助手逐项记子项（见 scripts/agent-probes.sh），加上原生 dashboard 的两项。
# /version 是网关自己答的命令，不要模型，验加密私聊和原生重启前后都能收发。
EXTRA_TIMEOUT=2400
extra_check() {
  hermes_check_dashboard
  agent_check_chat /version
  hermes_check_native_restart
  agent_check_model
  agent_check_mcp_bus
  agent_check_outsider
}

hermes_gateway() { running_container "wonton-$SMOKE_INSTANCE--$SMOKE_SLUG--gateway"; }

# 原生控制台：看得到在跑的网关进程，Channels 列得出四个平台。
hermes_check_dashboard() {
  local status channels
  hermes_token || { extra_item FAIL 原生控制台 "页面里没有注入的会话 token"; return 0; }
  status="$(hermes_dash GET /api/status)" || { extra_item FAIL 原生控制台 "/api/status 失败：$(agent_tail "$status")"; return 0; }
  jq -e '.gateway_running == true and (.gateway_pid | type == "number")' <<<"$status" >/dev/null ||
    { extra_item FAIL 原生控制台 "dashboard 看不到在跑的网关：$(agent_tail "$status")"; return 0; }
  channels="$(hermes_dash GET '/api/messaging/platforms?profile=default')" ||
    { extra_item FAIL 原生控制台 "Channels 接口失败：$(agent_tail "$channels")"; return 0; }
  if jq -e '[.platforms[].id] as $ids | ["telegram", "matrix", "feishu", "wecom"] | all(. as $id | $ids | index($id) != null)' \
      <<<"$channels" >/dev/null; then
    extra_item PASS 原生控制台 "dashboard 看得到在跑的网关，Channels 列出 telegram、matrix、feishu、wecom"
  else
    extra_item FAIL 原生控制台 "Channels 少了要的平台：$(agent_tail "$channels")"
  fi
}

# 原生重启：经 dashboard 重启网关进程（不经 docker restart），进程号要换、容器不重启，之后加密私聊照样通。
hermes_check_native_restart() {
  local gw before after status pid old i out
  agent_need 重启后还能聊 || return 0
  gw="$(hermes_gateway)" || { extra_item FAIL 重启后还能聊 "找不到 gateway 容器"; return 0; }
  hermes_token || { extra_item FAIL 重启后还能聊 "页面里没有注入的会话 token"; return 0; }
  old="$(hermes_dash GET /api/status | jq -r '.gateway_pid // empty')"
  before="$(docker inspect "$gw" --format '{{.Id}} {{.State.StartedAt}}')"
  pid="$(hermes_dash POST '/api/gateway/restart?profile=default' | jq -r 'select(.ok == true) | .pid // empty')"
  [[ -n "$pid" ]] || { extra_item FAIL 重启后还能聊 "dashboard 发起重启失败"; return 0; }
  for ((i = 0; i < 90; i++)); do
    status="$(hermes_dash GET '/api/actions/gateway-restart/status?lines=200')" || status=""
    jq -e '.running == false and .exit_code != null' <<<"$status" >/dev/null 2>&1 && break
    sleep 2
  done
  jq -e '.exit_code == 0' <<<"$status" >/dev/null 2>&1 ||
    { extra_item FAIL 重启后还能聊 "原生重启没成功：$(agent_tail "$status")"; return 0; }
  for ((i = 0; i < 90; i++)); do
    status="$(hermes_dash GET /api/status)" || status=""
    jq -e --arg old "$old" '.gateway_running == true and (.gateway_pid | tostring) != $old' <<<"$status" >/dev/null 2>&1 && break
    sleep 2
  done
  ((i < 90)) || { extra_item FAIL 重启后还能聊 "原生重启后 180 秒没有新的网关进程"; return 0; }
  agent_wait_healthy "$gw" || { extra_item FAIL 重启后还能聊 "原生重启后容器没回到 healthy"; return 0; }
  after="$(docker inspect "$gw" --format '{{.Id}} {{.State.StartedAt}}')"
  [[ "$before" == "$after" ]] || { extra_item FAIL 重启后还能聊 "原生重启把容器也重建或重启了"; return 0; }
  if out="$(agent_say --reply any --settle 5 --wait 180 --text /version 2>&1)"; then
    extra_item PASS 重启后还能聊 "经 dashboard 原生重启：网关进程号换了、容器没重启，之后加密私聊照样收发"
  else
    extra_item FAIL 重启后还能聊 "原生重启后收不到加密回复：$(agent_tail "$out")"
  fi
  printf '%s\n' "$out"
}

#!/usr/bin/env bash
# Sourced by the shared e2e/smoke.sh through plugins/hermes.sh, not a standalone runner.
# Keeps the harness's stack slot, installation, reporting and instance-only cleanup.
# Requires a prebuilt spec-0.7 core (base #180), tunnel-client, echo-bot and curl images;
# no image builds, upstream rewrites, host credentials or model-provider provisioning.
# Model settings must already expose a usable default route in this disposable instance.
# Missing model prerequisites are UNTESTED, never PASS; an authentication rejection stops
# the Agent checks without reading or changing any host credentials.
# Logs/results may contain private runtime replies: keep the harness work directory private.
#
# 原生控制台步骤（页面注入令牌、/api/status、/api/gateway/restart、Channels 端点）依赖
# 清单里的 webui 和规范 0.7 的默认 Bot 网页入口；网关重启走原生 dashboard，不经 docker restart。
# /version 的版本断言跟本仓库 compose 固定的上游镜像。
# Hook variables and lowercase port are supplied/consumed by the sourcing harness.
# shellcheck disable=SC2034,SC2154
SSO=none
MCP=0
IDENTITY_NA=0
E2E_NOTE="Native dashboard; paired owner tunnel; encrypted Matrix before/after native restart; injected default model and read-only MCP. "
hermes_preflight_images() {
  local image
  for image in "${WONTON_CORE_IMAGE:-wonton-core:e2e-main}" \
    "${HERMES_E2E_TUNNEL_IMAGE:-wonton-tunnel-client:dev}" \
    "${HERMES_E2E_ECHO_IMAGE:-wonton-echo-bot:dev}" \
    "${HERMES_E2E_CURL_IMAGE:-curlimages/curl:8.23.0}"; do
    if ! docker image inspect "$image" >/dev/null 2>&1; then
      printf 'Missing prebuilt image: %s. Supply the image before running the harness; no build/pull attempted.\n' "$image" >&2
      return 1
    fi
  done
}

# Run from before_install so shared smoke.sh can report manifest/spec rejection first.
# Never define build_images: all images are prebuilt, with no build/pull fallback.
# 卸预装不用这里做：2026-11-07 起 harness 在调用本钩子前自己卸掉核心预装的同 ID 插件
# （e2e/logs/hermes.log: "是核心预装的，先连数据卸掉预装的那份"），这里只做镜像预检。
before_install() {
  hermes_preflight_images || return 1
}

hermes_owned_container() {
  # 容器名由实例名派生（wonton-<实例>--…），Docker 名字全局唯一，存在即属于本次冒烟。
  # compose 建的核心容器不带 wonton.instance 标签（2026-11-07 实测），不能按标签校验。
  docker inspect "$1" --format '{{.Id}}' >/dev/null 2>>"$LOG"
}

hermes_wait_healthy() {
  local i state
  for ((i = 0; i < 90; i++)); do
    state="$(docker inspect "$HERMES_GW" --format '{{.State.Status}} {{.State.Health.Status}}' 2>>"$LOG")" || return 1
    [[ "$state" == 'running healthy' ]] && return 0
    [[ "$state" == exited* || "$state" == dead* ]] && return 1
    sleep 2
  done
  echo 'Gateway did not become healthy within 180 seconds' >&2
  return 1
}

hermes_dashboard_request() {
  local method="$1" path="$2"
  curl -sS --noproxy '*' --fail-with-body --max-time 30 \
    -b "$E2E_JAR" -c "$E2E_JAR" -X "$method" \
    -H "X-Hermes-Session-Token: $HERMES_DASH_TOKEN" "$HERMES_WEB_URL$path"
}

hermes_dashboard_page() {
  local page
  page="$(curl -sS --noproxy '*' --fail-with-body --max-time 30 -L \
    -b "$E2E_JAR" -c "$E2E_JAR" -H 'Accept: text/html' "$HERMES_WEB_URL/")" || return 1
  # Same page-injected session token and header as upstream web/src/lib/api.ts.
  # A raw fetch with no token is expected to fail even after the Wonton owner login.
  HERMES_DASH_TOKEN="$(python3 -c 'import re,sys
page=sys.stdin.read()
m=re.search(r"window\.__HERMES_SESSION_TOKEN__=\"([^\"]+)\"", page)
if not m or "<title>" not in page or "hermes" not in page.lower():
    raise SystemExit("Native Hermes page/session token missing")
print(m.group(1))' <<<"$page")" || return 1
  [[ -n "$HERMES_DASH_TOKEN" ]]
}

hermes_dashboard_smoke() {
  local plugins code identity status channels
  plugins="$(e2e_api GET /_wonton/api/plugins 2>>"$LOG")" || {
    fail 网页打得开 'Cannot list this instance plugins'; return 1;
  }
  HERMES_WEB_URL="$(jq -er --arg id "$E2E_NAME_ID" '.plugins[] | select(.id == $id) | .web_url | strings | select(length > 0)' <<<"$plugins")" || {
    fail 网页打得开 'No default-Bot web_url (requires core base #180/spec 0.7)'; return 1;
  }
  HERMES_WEB_URL="$(python3 -c 'import sys,urllib.parse
url=urllib.parse.urlsplit(sys.argv[1])
if url.scheme not in ("http", "https") or not url.netloc: raise SystemExit("Invalid plugin web URL")
print(urllib.parse.urlunsplit((url.scheme,url.netloc,"","","")))' "$HERMES_WEB_URL")" || return 1
  code="$(curl -sS --noproxy '*' --max-time 30 -o /dev/null -w '%{http_code}' \
    -H 'Accept: application/json' "$HERMES_WEB_URL/api/auth/me")" || {
    fail 网页打得开 'Anonymous dashboard probe could not connect'; return 1;
  }
  [[ "$code" == 401 ]] || { fail 网页打得开 "Anonymous identity endpoint returned $code, expected 401"; return 1; }
  curl -sS --noproxy '*' --fail-with-body --max-time 30 -o /dev/null -c "$E2E_JAR" \
    --data-urlencode "token=$E2E_TOKEN" "$E2E_BASE/_wonton/login" || {
    fail 主人身份能用 'Owner login failed'; return 1;
  }
  hermes_dashboard_page || { fail 网页打得开 'Native HTML/session token missing'; return 1; }
  identity="$(hermes_dashboard_request GET /api/auth/me)" || {
    fail 主人身份能用 'Native session-token identity request failed'; return 1;
  }
  jq -e '.user_id == "local" and .provider == "loopback"' <<<"$identity" >/dev/null || {
    fail 主人身份能用 'Unexpected native dashboard identity'; return 1;
  }
  status="$(hermes_dashboard_request GET /api/status)" || {
    fail 网页打得开 'Native status endpoint failed'; return 1;
  }
  jq -e '.gateway_running == true and (.gateway_pid | type == "number")' <<<"$status" >/dev/null || {
    fail 网页打得开 'Native dashboard does not see a running gateway'; return 1;
  }
  HERMES_BEFORE_PID="$(jq -r .gateway_pid <<<"$status")"
  channels="$(hermes_dashboard_request GET '/api/messaging/platforms?profile=default')" || {
    fail 网页打得开 'Native Channels endpoint failed'; return 1;
  }
  jq -e '[.platforms[].id] as $ids | ["telegram", "matrix", "feishu", "wecom"] | all(. as $id | $ids | index($id) != null)' <<<"$channels" >/dev/null || {
    fail 网页打得开 'Native Channels response omitted a required channel'; return 1;
  }
  pass 网页打得开 'Native Hermes HTML, authenticated status and Channels endpoints; anonymous API rejected (401)'
  pass 主人身份能用 'Wonton owner session reaches native loopback identity using page-injected token (not a separate Hermes account)'
}

hermes_pair_owner() {
  local endpoint pairing_output pairing credential_output i
  endpoint="$(e2e_api GET /_wonton/api/tunnel | jq -er '.endpoint_id | strings | select(length > 0)')" || return 1
  HERMES_PHONE="wonton-$E2E_INSTANCE-hermes-phone"
  # Never replace an existing resource. Harness owns E2E_AGENT_PHONE only after run succeeds.
  docker run -d --pull=never --name "$HERMES_PHONE" --label "wonton.instance=$E2E_INSTANCE" \
    --network "container:$HERMES_CORE" --entrypoint sh \
    "${HERMES_E2E_TUNNEL_IMAGE:-wonton-tunnel-client:dev}" -c \
    'tunnel-client --key /tmp/hermes-phone.key id && exec tunnel-client --key /tmp/hermes-phone.key proxy --auth --domain "$1" --server "$2" --addr "$3" --listen 127.0.0.1:18008' \
    sh "$E2E_INSTANCE.wonton.localhost" "$endpoint" "127.0.0.1:$((port + 1))" >>"$LOG" 2>&1 || return 1
  E2E_AGENT_PHONE="$HERMES_PHONE"
  export E2E_AGENT_PHONE
  for ((i = 0; i < 30; i++)); do
    credential_output="$(docker logs "$HERMES_PHONE" 2>&1)" || return 1
    HERMES_PROXY_CREDENTIAL="$(python3 -c 'import sys
lines=[s.removeprefix("会话凭据：").strip() for s in sys.stdin.read().splitlines() if s.startswith("会话凭据：")]
print(lines[-1] if lines else "")' <<<"$credential_output")" || return 1
    [[ -n "$HERMES_PROXY_CREDENTIAL" && "$credential_output" == *本地代理* ]] && break
    sleep 1
  done
  [[ -n "$HERMES_PROXY_CREDENTIAL" && "$credential_output" == *本地代理* ]] || return 1
  pairing_output="$(docker exec "$HERMES_CORE" wonton-core pair "127.0.0.1:$((port + 1))" 2>>"$LOG")" || return 1
  pairing="$(python3 -c 'import sys
lines=[s.strip() for s in sys.stdin.read().splitlines() if s.startswith("wonton://pair?")]
if not lines: raise SystemExit("Core did not provide a pairing URL")
print(lines[-1])' <<<"$pairing_output")" || return 1
  docker exec "$HERMES_PHONE" tunnel-client --key /tmp/hermes-phone.key pair \
    "$pairing" --name 'Hermes smoke' >>"$LOG" 2>&1
}

hermes_matrix_reply() {
  local text="$1" label="$2" login token out
  # Only this paired owner is allowed to obtain the Matrix JWT through the tunnel.
  login="$(docker run --rm --pull=never --name "wonton-$E2E_INSTANCE-hermes-login" \
    --label "wonton.instance=$E2E_INSTANCE" --network "container:$HERMES_PHONE" \
    "${HERMES_E2E_CURL_IMAGE:-curlimages/curl:8.23.0}" -sS --fail-with-body --max-time 30 \
    --noproxy '' -x http://127.0.0.1:18008 -U "$HERMES_PROXY_CREDENTIAL" \
    -H "Authorization: Bearer $E2E_TOKEN" -X POST \
    "http://$E2E_INSTANCE.wonton.localhost:$port/_wonton/matrix/login-token" 2>>"$LOG")" || return 1
  token="$(jq -er '.token | strings | select(length > 0)' <<<"$login")" || return 1
  out="$HERMES_WORK/$label.matrix.log"
  timeout 240 docker run --rm --pull=never --name "wonton-$E2E_INSTANCE-hermes-probe" \
    --label "wonton.instance=$E2E_INSTANCE" --network "container:$HERMES_PHONE" \
    "${HERMES_E2E_ECHO_IMAGE:-wonton-echo-bot:dev}" probe \
    --homeserver "http://matrix.$E2E_INSTANCE.wonton.localhost:$port" \
    --proxy "http://$HERMES_PROXY_CREDENTIAL@127.0.0.1:18008" --login-token "$token" \
    --room "$HERMES_ROOM" --bot "$HERMES_BOT" --reply any --settle 5 --wait 180 \
    --text "$text" >"$out" 2>&1 || { cat "$out" >>"$LOG"; return 1; }
  cat "$out" >>"$LOG"
  # /version is a real native gateway command: encryption/restart can be checked without
  # a model provider. Do not count an encrypted error message as a successful model turn.
  if [[ "$text" == /version ]]; then
    grep -qF '0.22.7' "$out"
  else
    grep -qF "$HERMES_MODEL_MARKER" "$out"
  fi
}

hermes_native_restart() {
  local action action_pid status i after container_before container_after
  container_before="$(docker inspect "$HERMES_GW" --format '{{.Id}} {{.State.StartedAt}}')" || return 1
  action="$(hermes_dashboard_request POST '/api/gateway/restart?profile=default')" || return 1
  action_pid="$(jq -er 'select(.ok == true and .name == "gateway-restart") | .pid | numbers' <<<"$action")" || return 1
  for ((i = 0; i < 90; i++)); do
    status="$(hermes_dashboard_request GET '/api/actions/gateway-restart/status?lines=200')" || return 1
    jq -e --argjson pid "$action_pid" '.pid == $pid and .name == "gateway-restart"' <<<"$status" >/dev/null || return 1
    if jq -e '.running == false and .exit_code != null' <<<"$status" >/dev/null; then
      printf '%s\n' "$status" >"$HERMES_WORK/restart.json"
      jq -e '.exit_code == 0' <<<"$status" >/dev/null || return 1
      break
    fi
    sleep 2
  done
  ((i < 90)) || { echo 'Native restart action timed out' >&2; return 1; }
  for ((i = 0; i < 90; i++)); do
    after="$(hermes_dashboard_request GET /api/status)" || return 1
    if jq -e --argjson old "$HERMES_BEFORE_PID" \
      '.gateway_running == true and (.gateway_pid | type == "number") and .gateway_pid != $old' <<<"$after" >/dev/null; then
      break
    fi
    sleep 2
  done
  ((i < 90)) || { echo 'Native restart did not produce a new live gateway PID' >&2; return 1; }
  hermes_wait_healthy || return 1
  container_after="$(docker inspect "$HERMES_GW" --format '{{.Id}} {{.State.StartedAt}}')" || return 1
  [[ "$container_before" == "$container_after" ]] || {
    echo 'Native restart unexpectedly replaced/restarted the container' >&2; return 1;
  }
  hermes_dashboard_page || return 1
  hermes_dashboard_request GET /api/auth/me | jq -e '.user_id == "local" and .provider == "loopback"' >/dev/null
}

hermes_model_sdk() {
  # Uses only this Bot's injected model environment and the SDK shipped in Hermes.
  # No list-models requirement: this Bot's key may be scoped to the default route only.
  timeout 180 docker exec -i -e HERMES_E2E_MARKER="$HERMES_MODEL_MARKER" "$HERMES_GW" \
    /opt/hermes/.venv/bin/python - <<'PY'
import os
import sys
import httpx
from openai import OpenAI, APIStatusError, APIConnectionError

required = ("WONTON_MODEL_BASE_URL", "WONTON_MODEL_API_KEY", "WONTON_MODEL_DEFAULT")
if any(not os.environ.get(key) for key in required):
    print("UNTESTED: Bot model environment is incomplete", file=sys.stderr)
    raise SystemExit(3)
marker = os.environ["HERMES_E2E_MARKER"]
try:
    with httpx.Client(trust_env=False, timeout=120) as transport:
        with OpenAI(base_url=os.environ[required[0]], api_key=os.environ[required[1]],
                    http_client=transport, max_retries=0) as client:
            response = client.chat.completions.create(
                model=os.environ[required[2]],
                messages=[{"role": "user", "content": "Reply with exactly: " + marker}],
                max_tokens=2048,
            )
    answer = response.choices[0].message.content or ""
    if answer.strip() != marker:
        raise SystemExit("FAIL: default-model response did not match the requested marker")
    print("Default model returned the requested marker via Hermes Python SDK")
except APIStatusError as exc:
    code = exc.status_code
    if code in (401, 403):
        print(f"Model API HTTP {code}: stop; no host credentials read or changed", file=sys.stderr)
        raise SystemExit(4)
    if code in (404, 503):
        print(f"UNTESTED: default model route unavailable (HTTP {code})", file=sys.stderr)
        raise SystemExit(3)
    print(f"FAIL: default-model request returned HTTP {code}", file=sys.stderr)
    raise SystemExit(1)
except APIConnectionError:
    print("UNTESTED: Bot cannot reach its injected model route", file=sys.stderr)
    raise SystemExit(3)
except Exception as exc:
    # Do not dump SDK request headers/URLs/credential-bearing exception bodies.
    print(f"FAIL: default-model SDK request failed ({type(exc).__name__})", file=sys.stderr)
    raise SystemExit(1)
PY
}

hermes_mcp_smoke() {
  local url
  url="$(docker exec "$HERMES_GW" printenv WONTON_MCP_URL 2>>"$LOG")" || return 1
  [[ -n "$url" ]] || return 1
  timeout 150 docker exec -i -e HTTP_PROXY= -e HTTPS_PROXY= -e ALL_PROXY= \
    -e http_proxy= -e https_proxy= -e all_proxy= -e NO_PROXY='*' -e no_proxy='*' \
    "$HERMES_GW" /opt/hermes/.venv/bin/python - list "$url" \
    <"$E2E_DIR/scripts/mcp_client.py" >"$HERMES_WORK/tools.json" 2>>"$LOG" || return 1
  jq -e 'any(.[]; .name == "wonton__list_plugins")' "$HERMES_WORK/tools.json" >/dev/null || return 1
  timeout 330 docker exec -i -e HTTP_PROXY= -e HTTPS_PROXY= -e ALL_PROXY= \
    -e http_proxy= -e https_proxy= -e all_proxy= -e NO_PROXY='*' -e no_proxy='*' \
    "$HERMES_GW" /opt/hermes/.venv/bin/python - call "$url" wonton__list_plugins '{}' \
    <"$E2E_DIR/scripts/mcp_client.py" >"$HERMES_WORK/plugins.json" 2>>"$LOG" || return 1
  jq -e --arg id "$E2E_NAME_ID" \
    '[.content[]? | select(.type == "text") | .text | fromjson | .plugins[]? | select(.id == $id)] | length > 0' \
    "$HERMES_WORK/plugins.json" >/dev/null
}

agent_smoke() {
  local model_rc=0 dashboard_ok=0 encrypted_ok=0 encrypted_failure=''
  HERMES_CORE="wonton-$E2E_INSTANCE--core"
  HERMES_GW="$(e2e_svc "$E2E_NAME_ID" gateway)"
  HERMES_WORK="$(mktemp -d "$E2E/work/hermes.$E2E_INSTANCE.XXXXXX")" || {
    fail 加密私聊 'Cannot create instance-specific evidence directory'; return;
  }
  say "Hermes evidence: $HERMES_WORK"
  if ! hermes_owned_container "$HERMES_CORE" || ! hermes_owned_container "$HERMES_GW"; then
    fail 加密私聊 'Core/gateway missing or not owned by this smoke instance'
    untested 模型能回话 'Instance ownership prerequisite failed'
    untested MCP工具 'Instance ownership prerequisite failed'
    return
  fi
  HERMES_ROOM="$(docker exec "$HERMES_GW" printenv MATRIX_HOME_ROOM)" || HERMES_ROOM=""
  HERMES_BOT="$(docker exec "$HERMES_GW" printenv MATRIX_USER_ID)" || HERMES_BOT=""
  if [[ "$HERMES_ROOM" != '!'* || "$HERMES_BOT" != '@'* ]] || ! hermes_wait_healthy; then
    fail 加密私聊 'Native Matrix environment/healthy gateway prerequisite failed'
    untested 模型能回话 'Gateway prerequisite failed'
    untested MCP工具 'Gateway prerequisite failed'
    return
  fi
  hermes_dashboard_smoke >>"$LOG" 2>&1 && dashboard_ok=1
  if hermes_pair_owner >>"$LOG" 2>&1 && hermes_matrix_reply /version before >>"$LOG" 2>&1; then
    encrypted_ok=1
    say 'Paired owner tunnel: encrypted native /version reply before restart succeeded'
  else
    encrypted_failure='Owner tunnel pairing or first encrypted native /version reply failed'
  fi
  if ((dashboard_ok)); then
    if hermes_native_restart >>"$LOG" 2>&1; then
      if ((encrypted_ok)) && hermes_matrix_reply /version after >>"$LOG" 2>&1; then
        say 'Native gateway PID changed without container restart; encrypted /version still works'
      else
        encrypted_failure="${encrypted_failure:+$encrypted_failure; }encrypted reply after native restart failed or pairing unavailable"
      fi
    else
      encrypted_failure="${encrypted_failure:+$encrypted_failure; }native dashboard restart failed (no Docker fallback)"
    fi
  else
    encrypted_failure="${encrypted_failure:+$encrypted_failure; }after-restart check blocked by native dashboard failure"
  fi
  if [[ -z "$encrypted_failure" ]]; then
    pass 加密私聊 'Paired owner tunnel: encrypted replies before/after successful native gateway restart; new PID, same container'
  else
    fail 加密私聊 "$encrypted_failure; see evidence/log"
  fi
  HERMES_MODEL_MARKER="HERMES_E2E_$E2E_INSTANCE"
  hermes_model_sdk >"$HERMES_WORK/model.log" 2>&1 || model_rc=$?
  cat "$HERMES_WORK/model.log" >>"$LOG"
  case "$model_rc" in
    0)
      if ((encrypted_ok)) && hermes_matrix_reply "Reply with exactly: $HERMES_MODEL_MARKER" model >>"$LOG" 2>&1; then
        pass 模型能回话 'Injected default route answered via Python SDK and native encrypted Matrix reply matched marker'
      elif ((encrypted_ok)); then
        fail 模型能回话 'SDK answered but native encrypted Matrix model reply failed/mismatched'
      else
        untested 模型能回话 'SDK answered, but encrypted Matrix path was unavailable; not a full Agent PASS'
      fi ;;
    3) untested 模型能回话 'Default model prerequisites unavailable; no provider provisioning attempted; see model.log' ;;
    4)
      fail 模型能回话 'Model API authentication rejected (401/403); stopped without host credentials or host changes'
      untested MCP工具 'Stopped after model authentication rejection'
      return ;;
    *) fail 模型能回话 'Real default-model SDK request failed; see model.log (no retries)' ;;
  esac
  if hermes_mcp_smoke >>"$LOG" 2>&1; then
    pass MCP工具 'Hermes Python MCP SDK: initialize, tools/list and read-only wonton__list_plugins returned this installed plugin'
  else
    fail MCP工具 'Hermes Python MCP SDK list/call failed or result omitted this installed plugin; see evidence/log'
  fi
}

#!/usr/bin/env bash
# scripts/smoke.sh 的一项一项探测，跟着插件走。由 harness source，变量由 harness 提供。
# shellcheck disable=SC2034  # DATA_NA 由 e2e/smoke.sh 读取
#
# Hermes（0.6.0 基线）清单没有 webui：核心不发 web_url，"网页打得开""主人身份能用"两项由
# harness 按"清单没有 webui"记 NA，web_assert / identity_check 没有可断言的页面，不写。
#
# 数据：data 钩子只允许 SMOKE_* 变量和 web_get / dweb，都是打插件网页的通道；没有 webui
# 就没有可写可读的实例数据。Bot 数据目录里能留下的只有 Matrix 会话加密状态和对话历史，
# 钩子约束内（无 docker exec、无 Matrix 配对）够不到；它们的存活由 scripts/e2e-hermes.sh
# 的 agent_smoke 覆盖：配对主人隧道后，加密 /version 在网关进程重启前后都成功，说明加密
# 状态确实从数据目录重载。因此声明 DATA_NA=1，升级保数据、重装保数据按声明记 NA。
DATA_NA=1

# 每次创建容器时把数据目录里的 config.yaml 修到和注入值一致（Hermes 自己也会改写它，这里只动核心管的三处），
# 另补一处插件默认值。
#
# 1) gateway.trust_env：Matrix 适配器 v0.22.5–v0.22.7 解析代理时不传目标主机，NO_PROXY 不生效；继承
#    HTTPS_PROXY 会把内部的 matrix.wonton.internal 发给出口代理而被 403 拒绝，所以关闭网关的环境代理。
# 2) providers.magpie：模型网关可选（spec：“models”是“有网关就用”）——三个模型变量都有值时保持启用（没有
#    enabled 字段就是启用）；都是空字符串（没接上网关）时标成 disabled，模型设置里不再显示这个不可用的
#    provider，模型要用户自己在设置里配；网关装好后核心重建容器，这里再把它启用回来。用户自己加的 provider
#    不动。enabled 字段是 Hermes 认的（hermes_cli.config_providers.is_provider_enabled）。
# 3) platforms.matrix.allow_admin_from：见下面第 3 块——占位符在这里换成注入值。
# 4) approvals.smart_policy：包里 config.yaml 定的守卫默认规则，已有 Bot 的配置缺这项或留空时补上，
#    主人自己写过的不动。
# 5) skills.disabled：包里 config.yaml 关掉的上游技能（现关 Obsidian 笔记技能），已有 Bot 的配置缺这项时补上，
#    主人自己写过的名单不动。
# 前三处注入值不写进包里的 config.yaml：已装好的 Bot 也要修到一致，只有 init 每次创建容器时能补（第 4、5 处
# 的默认值相反，就存在包里的 config.yaml，init 从那里取）。`hermes config set` 在这里用不了：它降权运行，
# 而这时数据目录还属于 root。
import os

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString

path = "/opt/data/config.yaml"
yaml = YAML()
with open(path) as f:
    config = yaml.load(f) or {}

changed = False

gateway = config.get("gateway") or {}
if gateway.get("trust_env") is not False:
    gateway["trust_env"] = False
    config["gateway"] = gateway
    changed = True

base = os.environ.get("WONTON_MODEL_BASE_URL", "").strip()
key = os.environ.get("WONTON_MODEL_API_KEY", "").strip()
default = os.environ.get("WONTON_MODEL_DEFAULT", "").strip()
providers = config.get("providers")
magpie = providers.get("magpie") if isinstance(providers, dict) else None
if isinstance(magpie, dict):
    if base and key and default:
        if magpie.pop("enabled", None) is not None:
            changed = True
    elif magpie.get("enabled") is not False:
        magpie["enabled"] = False
        changed = True

# 3) platforms.matrix.allow_admin_from：Hermes 只展开模型配置里的 ${VAR}，平台 extra 不展开；把名单占位符换成
#    注入名单的第一个 ID（第一个就是归属人），家人加进 Bot 听的名单也不会拿到斜杠命令管理员。已有 Bot 重建
#    容器时同样修到一致。
allowed = os.environ.get("WONTON_MATRIX_ALLOWED_USERS", "").strip().split(",")[0].strip()
platforms = config.get("platforms")
matrix = platforms.get("matrix") if isinstance(platforms, dict) else None
if isinstance(matrix, dict):
    admins = matrix.get("allow_admin_from")
    if isinstance(admins, str) and "${WONTON_MATRIX_ALLOWED_USERS}" in admins and allowed:
        matrix["allow_admin_from"] = allowed
        changed = True

# 4) approvals.smart_policy：默认值只存包里的 config.yaml 这一处（两份文本会漂移），补写时从这里取。
#    配置里已有非空的 smart_policy 视为主人自定，不动；留空和缺失都按没有自定处理。
with open("/seed/config.yaml") as f:
    seed = YAML().load(f) or {}
seed_policy = (seed.get("approvals") or {}).get("smart_policy")
if isinstance(seed_policy, str) and seed_policy.strip():
    approvals = config.get("approvals")
    if not isinstance(approvals, dict):
        approvals = {}
        config["approvals"] = approvals
    current = approvals.get("smart_policy")
    if not (isinstance(current, str) and current.strip()):
        # 字面量块写入，主人后来在 App 里编辑这份配置时读得清。
        approvals["smart_policy"] = LiteralScalarString(seed_policy)
        changed = True

# 5) skills.disabled：包里关掉的上游技能（包里 config.yaml 的注释写明原因），已有 Bot 的配置缺这项时补上。
#    和第 4 处一样只存包里一处；主人自己写过的名单不动。
seed_disabled = (seed.get("skills") or {}).get("disabled")
if isinstance(seed_disabled, list) and seed_disabled:
    skills = config.get("skills")
    if not isinstance(skills, dict):
        skills = {}
        config["skills"] = skills
    if "disabled" not in skills:
        skills["disabled"] = seed_disabled
        changed = True

if changed:
    with open(path + ".tmp", "w") as f:
        yaml.dump(config, f)
    stat = os.stat(path)
    os.chown(path + ".tmp", stat.st_uid, stat.st_gid)
    os.chmod(path + ".tmp", stat.st_mode)
    os.replace(path + ".tmp", path)

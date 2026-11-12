# 每次创建容器时把数据目录里的 config.yaml 修到和注入值一致（Hermes 自己也会改写它，这里只动核心管的三处），
# 另把包里 config.yaml 定的默认值补给已有 Bot。
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
# 5) skills.disabled：包里关掉的上游技能（包里 config.yaml 的注释写明原因），已有 Bot 的配置缺这项时补上，
#    主人自己写过的名单不动。
# 6) display.busy_input_mode：旧包写的 queue（排队），包里改成 steer（忙时新消息递进当前这一轮）后，已装
#    Bot 重建容器时跟着换成包里的取值；只认旧包写的 queue，主人自己改过的取值（如 interrupt）不动。
# 7) display.platforms.matrix.interim_assistant_messages：旧包写的 true（模型调用工具前说的那句话先发出去，
#    一轮里一句一条、每句都响铃），包里改成 false（一轮结束才发完整回答），已装 Bot 重建容器时跟着换；
#    只认旧包写的 true，主人自己改过的不动。
# 8) 国内 IM 渠道开关（飞书、钉钉、企业微信、QQ、个人微信，插件设置里每渠道一个）：开关关着在 config.yaml 写显式
#    停用（优先于凭据），开关打开撤掉，启用与否由 Hermes 按凭据判定（见 apply 里的第 8 块）。
# 前三处注入值不写进包里的 config.yaml：已装好的 Bot 也要修到一致，只有 init 每次创建容器时能补。第 4-7 处
# 的默认值就存在包里的 config.yaml，init 从那里取。`hermes config set` 在这里用不了：它降权运行，而这时
# 数据目录还属于 root。
import os

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString

CONFIG_PATH = "/opt/data/config.yaml"
SEED_PATH = "/seed/config.yaml"

# 国内 IM 渠道（插件设置里每个渠道一个开关，默认关）。config.yaml 的键名是 Hermes 平台 ID。
_IM_CHANNELS = {
    "feishu": "FEISHU_WONTON_ENABLED",
    "dingtalk": "DINGTALK_WONTON_ENABLED",
    "wecom": "WECOM_WONTON_ENABLED",
    "qqbot": "QQ_WONTON_ENABLED",
    "weixin": "WEIXIN_WONTON_ENABLED",
}


def _follow_pack(config, seed, path, old_value):
    """把旧包写进配置的默认值换成包里现在的取值：只有配置里的值还是旧默认时才动，主人自己改过的
    不动。path 是键路径（如 ("display", "busy_input_mode")），取值按字面比较（布尔和 true/false 串
    都算旧默认的写法）。"""
    seed_value = seed
    for key in path:
        seed_value = seed_value.get(key) if isinstance(seed_value, dict) else None
    if seed_value is None or isinstance(seed_value, (list, dict)):
        return False
    node = config
    for key in path[:-1]:
        node = node.get(key) if isinstance(node, dict) else None
    if not isinstance(node, dict):
        return False
    current = node.get(path[-1])
    if str(current).strip().lower() != str(old_value).strip().lower():
        return False
    if str(current).strip().lower() == str(seed_value).strip().lower():
        return False
    node[path[-1]] = seed_value
    return True


def apply(config, seed) -> bool:
    """把注入值和包里的默认值修进 config（就地改），返回是否改过。"""
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

    # 6) display.busy_input_mode：见文件头。只把旧包写的 queue 换成包里的取值，主人自己改过的不动。
    # 7) display.platforms.matrix.interim_assistant_messages：旧包写的 true（模型调用工具前说的那句话先发出去，
    #    一句一条、每句都响铃），包里改成 false（一轮结束才发完整回答），已装 Bot 重建容器时跟着换。
    if _follow_pack(config, seed, ("display", "busy_input_mode"), "queue"):
        changed = True
    if _follow_pack(config, seed,
                    ("display", "platforms", "matrix", "interim_assistant_messages"), "true"):
        changed = True

    # 8) 国内 IM 渠道开关（文件头 _IM_CHANNELS）。Hermes 会自动启用「环境里有凭据」的渠道，所以关着的开关要在
    #    config.yaml 里显式停用——显式停用优先于凭据（上游保证），开关关着时渠道完全不启用，设置里留着凭据也一样；
    #    开关打开时撤掉显式停用，交给 Hermes 按凭据判定：Wonton 注入的凭据、或主人经原生 Channels 页/终端向导写进
    #    .env 的凭据都能启用，两处都没有就保持未启用、不报错。个人微信没有可填的凭据字段：扫码登录写入 .env 后
    #    打开开关即可。
    platforms = config.get("platforms")
    if not isinstance(platforms, dict):
        platforms = {}
        config["platforms"] = platforms
    for name, env in _IM_CHANNELS.items():
        entry = platforms.get(name)
        if os.environ.get(env, "").strip().lower() == "true":
            # 开关打开：只撤掉残留的显式停用；没有凭据时 Hermes 自己保持未启用，不写空条目。
            if isinstance(entry, dict) and entry.pop("enabled", None) is not None:
                changed = True
        else:
            if not isinstance(entry, dict):
                entry = {}
                platforms[name] = entry
            if entry.get("enabled") is not False:
                entry["enabled"] = False
                changed = True

    return changed


def main():
    yaml = YAML()
    with open(CONFIG_PATH) as f:
        config = yaml.load(f) or {}
    with open(SEED_PATH) as f:
        seed = yaml.load(f) or {}
    if not apply(config, seed):
        return
    with open(CONFIG_PATH + ".tmp", "w") as f:
        yaml.dump(config, f)
    stat = os.stat(CONFIG_PATH)
    os.chown(CONFIG_PATH + ".tmp", stat.st_uid, stat.st_gid)
    os.chmod(CONFIG_PATH + ".tmp", stat.st_mode)
    os.replace(CONFIG_PATH + ".tmp", CONFIG_PATH)


if __name__ == "__main__":
    main()

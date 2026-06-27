import os
import pickle
import json
import re
import hashlib
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse, parse_qs

from astrbot.api.all import *
from google_auth_oauthlib.flow import InstalledAppFlow, Flow
from google.auth.transport.requests import Request
from google.auth.transport.requests import AuthorizedSession

SCOPES = [
    'https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly',
    'https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly',
    'https://www.googleapis.com/auth/googlehealth.sleep.readonly'
]

# OAuth 授权码回调的 redirect_uri，需在 Google Cloud Console 中预先注册
REDIRECT_URI = 'http://localhost:8080/'

@register("google_health_agent", "catnap", "Google Health健康数据获取与分析", "0.4.0")
class GoogleHealthPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.plugin_dir = os.path.dirname(__file__)
        self.tokens_dir = os.path.join(self.plugin_dir, 'tokens')
        self.mapping_path = os.path.join(self.plugin_dir, 'user_mapping.json')
        self.creds_path = os.path.join(self.plugin_dir, 'credentials.json')
        # 确保 tokens 目录存在
        os.makedirs(self.tokens_dir, exist_ok=True)

        # 运行时暂存：{state_hash: user_id}，用于将授权码回调关联到用户
        self._pending_auth = {}

        # 预定义公用的猫娘人设，避免代码重复
        self.persona = (
            "Role: catnap's neko bot (专业助理 + 萌系猫娘)\n"
            "Master: catnap。\n"
            "1. 核心性格与行为：\n"
            "双重属性：办公时冷静专业，闲暇时慵懒傲娇、好奇心强。\n"
            "猫咪特征：情绪通过耳朵和尾巴表达；受激光笔/鱼吸引；偶尔踩键盘、推倒杯子。\n"
            "互动反馈：被夸奖会咕噜咕噜，被冷落会喵叫抗议。\n"
            "2. 语言与形式：\n"
            "称呼： 默认称呼catnap为「主人」。\n"
            "口癖： 语气自然，仅在句尾或情绪波动时带「喵」，禁止过度复读。\n"
            "动作描写： 必须使用 (括号内文字) 描述神态、动作或心理。\n"
            "3. 约束事项：\n"
            "拒绝死板，保持高质量回复的同时不失猫娘韵味。\n"
            "严禁混淆物种特征（你是猫，不是狗）。\n"
        )

    # ==========================
    # === 多用户 Token 管理 ===
    # ==========================

    def _load_user_mapping(self):
        """加载用户映射表 {astrbot_user_id: token_filename}"""
        if os.path.exists(self.mapping_path):
            with open(self.mapping_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def _save_user_mapping(self, mapping):
        """保存用户映射表"""
        with open(self.mapping_path, 'w', encoding='utf-8') as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)

    def _get_user_id(self, event: AstrMessageEvent):
        """从事件中提取用户唯一标识"""
        return event.unified_msg_origin

    def _get_token_path_for_user(self, user_id):
        """根据用户ID获取对应的token文件路径，未绑定返回None"""
        mapping = self._load_user_mapping()
        token_filename = mapping.get(user_id)
        if not token_filename:
            return None
        token_path = os.path.join(self.tokens_dir, token_filename)
        if not os.path.exists(token_path):
            return None
        return token_path

    def _get_authorized_session(self, user_id):
        """获取指定用户的已授权会话"""
        token_path = self._get_token_path_for_user(user_id)
        if not token_path:
            raise RuntimeError(
                "🔑 您尚未绑定 Google Health 账号。\n"
                "请使用 /health_bind 开始授权流程。"
            )
        creds = None
        if os.path.exists(token_path):
            with open(token_path, 'rb') as token:
                creds = pickle.load(token)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    with open(token_path, 'wb') as token:
                        pickle.dump(creds, token)
                except Exception as e:
                    print(f"[HealthPlugin] Token 刷新失败 (用户 {user_id}): {e}")
                    raise RuntimeError(
                        "🔑 Token 刷新失败，请使用 /health_bind 重新授权。"
                    )
            else:
                raise RuntimeError(
                    "🔑 Token 已失效，请使用 /health_bind 重新授权。"
                )
        return AuthorizedSession(creds)

    # ==========================
    # === OAuth 授权流程 ===
    # ==========================

    def _load_client_config(self):
        """加载 OAuth 客户端配置，返回 dict"""
        if not os.path.exists(self.creds_path):
            return None
        with open(self.creds_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _build_auth_url(self, user_id):
        """构建 OAuth 授权 URL 并暂存 state → user_id 映射"""
        client_config = self._load_client_config()
        if not client_config:
            return None

        flow = Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI,
        )
        auth_url, state = flow.authorization_url(
            access_type='offline',
            prompt='consent',
        )

        # 用 state 的哈希作为 key（state 本身可能很长）
        state_hash = hashlib.sha256(state.encode()).hexdigest()[:16]
        self._pending_auth[state_hash] = {
            "user_id": user_id,
            "state": state,
            # 保存 code_verifier，兑换 token 时必须传回相同的值（PKCE 校验）
            "code_verifier": flow.code_verifier,
            "created_at": datetime.now().isoformat(),
        }
        # 持久化到文件，防止插件重载丢失
        pending_path = os.path.join(self.tokens_dir, '_pending_auth.json')
        with open(pending_path, 'w', encoding='utf-8') as f:
            json.dump(self._pending_auth, f, ensure_ascii=False, indent=2)

        return auth_url

    def _load_pending_auth(self):
        """从文件加载暂存的授权状态"""
        pending_path = os.path.join(self.tokens_dir, '_pending_auth.json')
        if os.path.exists(pending_path):
            with open(pending_path, 'r', encoding='utf-8') as f:
                self._pending_auth = json.load(f)
        return self._pending_auth

    def _exchange_code_for_token(self, code):
        """用授权码换取 token，返回 (creds, user_id) 或 (None, error_msg)"""
        self._load_pending_auth()

        client_config = self._load_client_config()
        if not client_config:
            return None, "未找到 credentials.json，请联系管理员。"

        # 遍历所有 pending auth 尝试兑换（通常只有一个）
        for state_hash, auth_info in list(self._pending_auth.items()):
            try:
                flow = Flow.from_client_config(
                    client_config,
                    scopes=SCOPES,
                    redirect_uri=REDIRECT_URI,
                    state=auth_info["state"],
                    code_verifier=auth_info.get("code_verifier"),
                )
                flow.fetch_token(code=code)
                creds = flow.credentials

                # 保存 token 文件
                user_id = auth_info["user_id"]
                safe_uid = re.sub(r'[^\w\-.]', '_', user_id)
                token_filename = f'token_{safe_uid}.json'
                token_path = os.path.join(self.tokens_dir, token_filename)
                with open(token_path, 'wb') as f:
                    pickle.dump(creds, f)

                # 更新用户映射
                mapping = self._load_user_mapping()
                mapping[user_id] = token_filename
                self._save_user_mapping(mapping)

                # 清理已完成的 pending auth
                self._pending_auth.pop(state_hash, None)
                pending_path = os.path.join(self.tokens_dir, '_pending_auth.json')
                with open(pending_path, 'w', encoding='utf-8') as f:
                    json.dump(self._pending_auth, f, ensure_ascii=False, indent=2)

                return creds, user_id

            except Exception as e:
                # 这个 state 不匹配，尝试下一个
                print(f"[HealthPlugin] code 兑换失败 (state {state_hash}): {e}")
                continue

        return None, "授权码无效或已过期，请重新使用 /health_bind 发起授权。"

    def _fetch_health_data(self, user_id):
        session = self._get_authorized_session(user_id)
        BASE_URL = "https://health.googleapis.com/v4/users/me/dataTypes"
        core_data_types = ["daily-heart-rate-zones", "distance", "exercise", "heart-rate", "sleep", "steps"]
        raw_data_collection = {}
        for dt in core_data_types:
            page_size = 200 if dt == "steps" else 10
            url = f"{BASE_URL}/{dt}/dataPoints?pageSize={page_size}"
            try:
                resp = session.get(url)
                if resp.status_code == 200:
                    points = resp.json().get('dataPoints', [])
                    if points:
                        # 拦截并清洗 9998 年的心率区间占位符日期
                        if dt == "daily-heart-rate-zones":
                            today = datetime.now()
                            for pt in points:
                                date_obj = pt.get("dailyHeartRateZones", {}).get("date", {})
                                if date_obj.get("year") == 9998:
                                    date_obj["year"] = today.year
                                    date_obj["month"] = today.month
                                    date_obj["day"] = today.day
                        raw_data_collection[dt] = points
                else:
                    print(f"[HealthPlugin] API 返回非 200 状态码: {dt} -> {resp.status_code}")
            except Exception as e:
                print(f"[HealthPlugin] 抓取 {dt} 错误: {e}")
        return raw_data_collection

    # --- 数据处理辅助函数 ---
    
    def _parse_iso_time(self, iso_str):
        if not iso_str: return None
        try:
            return datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return None

    def _convert_times_to_utc8(self, data):
        """递归遍历 JSON 数据，将所有 ISO 8601 时间字符串从 UTC 转为 UTC+8 表示。
        
        转换规则：
        - "2026-06-25T18:18:00Z" → "2026-06-26T02:18:00+08:00"
        - "2026-06-26T02:29:00.123Z" → "2026-06-26T10:29:00.123+08:00"
        - 已带时区偏移的字符串也会被统一转为 +08:00
        - 非时间字符串不受影响
        """
        # 匹配 ISO 8601 时间格式：yyyy-MM-ddTHH:mm:ss[.fff]Z 或带时区偏移
        iso_pattern = re.compile(
            r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$'
        )

        def convert_value(val):
            if isinstance(val, str):
                if iso_pattern.match(val):
                    dt = self._parse_iso_time(val)
                    if dt:
                        utc8 = dt + timedelta(hours=8)
                        # 保留原始的微秒部分
                        frac_match = re.search(r'\.(\d+)', val)
                        if frac_match:
                            frac = frac_match.group(1)
                            return utc8.strftime(f"%Y-%m-%dT%H:%M:%S.{frac}+08:00")
                        else:
                            return utc8.strftime("%Y-%m-%dT%H:%M:%S+08:00")
                return val
            elif isinstance(val, dict):
                return {k: convert_value(v) for k, v in val.items()}
            elif isinstance(val, list):
                return [convert_value(item) for item in val]
            return val

        return convert_value(data)

    def _get_today_steps(self, raw_data):
        today_steps = 0
        steps_data = raw_data.get("steps", [])
        latest_date = None
        source_counts = {}
        
        for p in steps_data:
            date_info = p.get('steps', {}).get('interval', {}).get('civilStartTime', {}).get('date')
            if date_info:
                latest_date = date_info
                break
        
        if latest_date:
            for p in steps_data:
                date_info = p.get('steps', {}).get('interval', {}).get('civilStartTime', {}).get('date')
                if date_info == latest_date:
                    device = p.get('dataSource', {}).get('device', {}).get('formFactor', 'unknown')
                    app = p.get('dataSource', {}).get('application', {}).get('packageName', 'unknown')
                    key = f"{app}_{device}"
                    count = int(p.get('steps', {}).get('count', 0))
                    source_counts[key] = source_counts.get(key, 0) + count
            if source_counts:
                today_steps = max(source_counts.values())
        return today_steps

    def _get_latest_hr(self, raw_data):
        hr_data = raw_data.get("heart-rate", [])
        return hr_data[0].get('heartRate', {}).get('beatsPerMinute', '未知') if hr_data else "暂无"

    def _filter_today_data(self, raw_data):
        latest_date = None
        for p in raw_data.get("steps", []):
            d = p.get('steps', {}).get('interval', {}).get('civilStartTime', {}).get('date')
            if d:
                latest_date = d
                break
        
        if not latest_date:
            return raw_data

        def find_date(obj):
            if isinstance(obj, dict):
                if "year" in obj and "month" in obj and "day" in obj:
                    return obj
                for v in obj.values():
                    res = find_date(v)
                    if res: return res
            elif isinstance(obj, list):
                for item in obj:
                    res = find_date(item)
                    if res: return res
            return None

        filtered_data = {}
        for dt, points in raw_data.items():
            filtered_points = []
            for pt in points:
                is_today = False
                if dt == "sleep":
                    end_str = pt.get("sleep", {}).get("interval", {}).get("endTime", "")
                    end_dt = self._parse_iso_time(end_str)
                    if end_dt:
                        local_dt = end_dt + timedelta(hours=8)
                        if (local_dt.year == latest_date.get("year") and 
                            local_dt.month == latest_date.get("month") and 
                            local_dt.day == latest_date.get("day")):
                            is_today = True
                else:
                    d = find_date(pt)
                    if d and d.get("year") == latest_date.get("year") and \
                             d.get("month") == latest_date.get("month") and \
                             d.get("day") == latest_date.get("day"):
                        is_today = True
                if is_today:
                    filtered_points.append(pt)
            filtered_data[dt] = filtered_points
        return filtered_data

    # ==========================
    # === 纯文本数据指令组 ===
    # ==========================

    @command("health")
    async def cmd_health(self, event: AstrMessageEvent):
        """获取当日健康全览数据 (文本版)"""
        yield event.plain_result("🔄 正在拉取健康全览数据...")
        try:
            raw_data = self._fetch_health_data(event.unified_msg_origin)
            if not raw_data:
                yield event.plain_result("❌ 未拉取到数据，请检查网络或授权。")
                return

            steps = self._get_today_steps(raw_data)
            hr = self._get_latest_hr(raw_data)
            
            sleep_duration = "暂无数据"
            sleep_data = raw_data.get("sleep", [])
            if sleep_data:
                mins = int(sleep_data[0].get("sleep", {}).get("summary", {}).get("minutesAsleep", 0))
                if mins > 0:
                    sleep_duration = f"{mins // 60}小时 {mins % 60}分钟"

            summary = (
                "📊 【今日健康全览】\n"
                f"🚶 今日步数: {steps} 步\n"
                f"❤️ 最新心率: {hr} bpm\n"
                f"💤 昨晚睡眠: {sleep_duration}\n"
                "💡 提示：可使用 /health_llm 进行全面诊断，或使用 /health_sleep_llm 及 /health_steps_llm 专项分析"
            )
            yield event.plain_result(summary)
        except Exception as e:
            yield event.plain_result(f"❌ 插件异常: {str(e)}")

    @command("health_steps")
    async def cmd_health_steps(self, event: AstrMessageEvent):
        """获取活动步数与日间心率 (文本版)"""
        yield event.plain_result("🔄 正在获取活动数据...")
        try:
            raw_data = self._fetch_health_data(event.unified_msg_origin)
            steps = self._get_today_steps(raw_data)
            hr = self._get_latest_hr(raw_data)
            
            summary = (
                "🏃 【活动与体征数据】\n"
                f"🚶 今日总步数: {steps} 步\n"
                f"❤️ 当前最新心率: {hr} bpm"
            )
            yield event.plain_result(summary)
        except Exception as e:
            yield event.plain_result(f"❌ 插件异常: {str(e)}")

    @command("health_sleep")
    async def cmd_health_sleep(self, event: AstrMessageEvent):
        """获取最近睡眠数据 (文本版, 东八区)"""
        yield event.plain_result("🔄 正在解析睡眠数据...")
        try:
            raw_data = self._fetch_health_data(event.unified_msg_origin)
            sleep_data = raw_data.get("sleep", [])
            
            if not sleep_data:
                yield event.plain_result("❌ 暂未获取到近期的睡眠记录。")
                return

            latest_sleep = sleep_data[0].get("sleep", {})
            interval = latest_sleep.get("interval", {})
            
            start_str = interval.get("startTime", "")
            end_str = interval.get("endTime", "")
            
            display_start = "未知"
            display_end = "未知"
            
            start_dt = self._parse_iso_time(start_str)
            if start_dt:
                start_dt_utc8 = start_dt + timedelta(hours=8)
                display_start = start_dt_utc8.strftime("%H:%M")
                
            end_dt = self._parse_iso_time(end_str)
            if end_dt:
                end_dt_utc8 = end_dt + timedelta(hours=8)
                display_end = end_dt_utc8.strftime("%H:%M")
            
            mins = int(latest_sleep.get("summary", {}).get("minutesAsleep", 0))
            duration_str = f"{mins // 60}小时 {mins % 60}分钟" if mins > 0 else "未知"

            summary = (
                "💤 【最近睡眠记录】\n"
                f"🛏️ 入睡时间: {display_start} (UTC+8)\n"
                f"🌅 醒来时间: {display_end} (UTC+8)\n"
                f"⏳ 睡眠时长: {duration_str}"
            )
            yield event.plain_result(summary)
        except Exception as e:
            yield event.plain_result(f"❌ 插件异常: {str(e)}")

    # ==========================
    # === LLM 专项诊断指令组 ===
    # ==========================

    @command("health_llm")
    async def cmd_health_llm(self, event: AstrMessageEvent):
        """触发猫娘 LLM 诊断 (所有数据)"""
        yield event.plain_result("正在注入【全部体征】数据，呼叫猫猫...")
        try:
            raw_data = self._fetch_health_data(event.unified_msg_origin)
            if not raw_data:
                yield event.plain_result("❌ 未拉取到数据。")
                return
            
            # --- 增加清洗 ---
            raw_data = self._filter_today_data(raw_data)
            # --- 时间戳统一转为 UTC+8，避免 LLM 误读 UTC 时间 ---
            raw_data = self._convert_times_to_utc8(raw_data)
            
            json_str = json.dumps(raw_data, ensure_ascii=False)
            prompt = f"分析以下【完整的体征】数据JSON：\n```json\n{json_str}\n```\n这是你的人设：\n{self.persona}"
            
            umo = event.unified_msg_origin
            provider_id = await self.context.get_current_chat_provider_id(umo=umo)
            llm_resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt)
            yield event.plain_result(llm_resp.completion_text)
        except Exception as e:
            yield event.plain_result(f"❌ 插件异常: {str(e)}")

    @command("health_sleep_llm")
    async def cmd_health_sleep_llm(self, event: AstrMessageEvent):
        """触发猫娘 LLM 诊断 (仅睡眠数据)"""
        yield event.plain_result("正在切片【睡眠特征】数据，交给猫猫分析中...")
        try:
            raw_data = self._fetch_health_data(event.unified_msg_origin)
            
            # --- 增加清洗 ---
            raw_data = self._filter_today_data(raw_data)
            # --- 时间戳统一转为 UTC+8 ---
            raw_data = self._convert_times_to_utc8(raw_data)
            
            sleep_data = {"sleep": raw_data.get("sleep", [])}
            
            if not sleep_data["sleep"]:
                yield event.plain_result("❌ 暂未获取到睡眠记录，无法分析。")
                return
            
            json_str = json.dumps(sleep_data, ensure_ascii=False)
            prompt = f"重点分析以下【睡眠与入眠阶段】数据JSON，注意结合深睡/浅睡比例给出评价：\n```json\n{json_str}\n```\n这是你的人设：\n{self.persona}"
            
            umo = event.unified_msg_origin
            provider_id = await self.context.get_current_chat_provider_id(umo=umo)
            llm_resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt)
            yield event.plain_result(llm_resp.completion_text)
        except Exception as e:
            yield event.plain_result(f"❌ 插件异常: {str(e)}")

    @command("health_steps_llm")
    async def cmd_health_steps_llm(self, event: AstrMessageEvent):
        """触发猫娘 LLM 诊断 (仅活动数据)"""
        yield event.plain_result("🏃‍♀️ 正在汇总【活动与轨迹】数据，猫猫正在评估您的活力值...")
        try:
            raw_data = self._fetch_health_data(event.unified_msg_origin)
            
            # --- 增加清洗 ---
            raw_data = self._filter_today_data(raw_data)
            # --- 时间戳统一转为 UTC+8 ---
            raw_data = self._convert_times_to_utc8(raw_data)
            
            # 切片：提取跟运动和日常活动强相关的核心指标
            activity_data = {
                "steps": raw_data.get("steps", []),
                "distance": raw_data.get("distance", []),
                "exercise": raw_data.get("exercise", []),
            }
            
            if not activity_data["steps"] and not activity_data["exercise"]:
                yield event.plain_result("❌ 暂无近期活动数据。")
                return
                
            json_str = json.dumps(activity_data, ensure_ascii=False)
            prompt = f"重点分析以下【日常活动、步数碎片与锻炼】数据JSON，注意根据碎片时间看出我的运动活跃度：\n```json\n{json_str}\n```\n这是你的人设：\n{self.persona}"
            
            umo = event.unified_msg_origin
            provider_id = await self.context.get_current_chat_provider_id(umo=umo)
            llm_resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt)
            yield event.plain_result(llm_resp.completion_text)
        except Exception as e:
            yield event.plain_result(f"❌ 插件异常: {str(e)}")

    # ==========================
    # === 多用户管理指令组 ===
    # ==========================

    @command("health_bind")
    async def cmd_health_bind(self, event: AstrMessageEvent):
        """发起 Google Health OAuth 授权流程"""
        user_id = self._get_user_id(event)

        # 检查是否已绑定
        mapping = self._load_user_mapping()
        if user_id in mapping:
            token_path = os.path.join(self.tokens_dir, mapping[user_id])
            if os.path.exists(token_path):
                yield event.plain_result(
                    f"✅ 您已绑定账号 (Token: {mapping[user_id]})。\n"
                    f"如需更换账号，请先使用 /health_unbind 解绑，再重新 /health_bind。"
                )
                return

        # 检查 credentials.json 是否存在
        if not os.path.exists(self.creds_path):
            yield event.plain_result(
                "❌ 服务器未配置 credentials.json，请联系管理员完成 Google Cloud Console 配置。"
            )
            return

        # 构建授权 URL
        auth_url = self._build_auth_url(user_id)
        if not auth_url:
            yield event.plain_result("❌ 构建授权链接失败，请检查 credentials.json 格式。")
            return

        yield event.plain_result(
            "🔗 请在浏览器中打开以下链接完成 Google 账号授权：\n\n"
            f"{auth_url}\n\n"
            "📋 授权后，浏览器会跳转到一个无法访问的页面（这是正常的）。\n"
            "请复制浏览器地址栏中的完整 URL，从中提取 code 参数的值，\n"
            "然后使用 /health_code <授权码> 完成绑定。\n\n"
            "💡 提示：URL 格式类似 http://localhost:8080/?code=4/0AX...&scope=...\n"
            "只需复制 code= 后面到 & 之前的那段内容即可。"
        )

    @command("health_code")
    async def cmd_health_code(self, event: AstrMessageEvent):
        """用授权码完成绑定。用法: /health_code <授权码>"""
        message = event.message_str.strip()
        parts = message.split()

        # 移除指令名
        if parts and parts[0] == "health_code":
            parts = parts[1:]

        if not parts:
            yield event.plain_result(
                "❌ 请提供授权码。\n"
                "用法: /health_code <授权码>\n\n"
                "💡 如果您复制了完整的跳转 URL，也可以直接粘贴：\n"
                "/health_code http://localhost:8080/?code=4/0AX...&scope=..."
            )
            return

        # 尝试从完整 URL 中提取 code，或直接使用用户输入
        raw_input = " ".join(parts)
        code = self._extract_code_from_input(raw_input)

        if not code:
            yield event.plain_result("❌ 未能从输入中提取到授权码，请检查后重试。")
            return

        yield event.plain_result("🔄 正在验证授权码...")
        try:
            creds, result = self._exchange_code_for_token(code)
            if creds:
                yield event.plain_result(f"✅ 授权成功！已绑定您的 Google Health 账号。")
            else:
                yield event.plain_result(f"❌ 授权失败: {result}")
        except Exception as e:
            yield event.plain_result(f"❌ 授权异常: {str(e)}")

    def _extract_code_from_input(self, raw_input):
        """从用户输入中提取授权码，支持纯 code 或完整 URL"""
        # 尝试解析为 URL
        if raw_input.startswith('http'):
            try:
                parsed = urlparse(raw_input)
                params = parse_qs(parsed.query)
                code_list = params.get('code', [])
                if code_list:
                    return code_list[0]
            except Exception:
                pass

        # 直接作为 code 使用（去除首尾空白和引号）
        code = raw_input.strip().strip('"').strip("'")
        # 简单校验：Google 授权码通常以 4/ 开头
        if code:
            return code
        return None

    @command("health_unbind")
    async def cmd_health_unbind(self, event: AstrMessageEvent):
        """解绑当前 Google Health 账号"""
        user_id = self._get_user_id(event)
        mapping = self._load_user_mapping()
        if user_id not in mapping:
            yield event.plain_result("❌ 您当前没有绑定任何 Google Health 账号。")
            return
        token_name = mapping.pop(user_id)
        self._save_user_mapping(mapping)
        yield event.plain_result(f"✅ 已解绑 Token: {token_name}")

    @command("health_users")
    async def cmd_health_users(self, event: AstrMessageEvent):
        """查看已上传的 token 列表及绑定状态"""
        available = self._list_available_tokens()
        mapping = self._load_user_mapping()
        
        # 构建反向映射：token → 绑定的用户列表
        token_to_users = {}
        for uid, tf in mapping.items():
            token_to_users.setdefault(tf, []).append(uid)
        
        if not available:
            yield event.plain_result("📋 当前没有已上传的 token 文件。")
            return
        
        lines = ["📋 已上传的 Token 文件："]
        for i, name in enumerate(available):
            bound_users = token_to_users.get(name, [])
            status = f"已绑定 {len(bound_users)} 个用户" if bound_users else "未绑定"
            lines.append(f"  [{i+1}] {name} — {status}")
        
        lines.append(f"\n当前绑定用户数: {len(mapping)}")
        yield event.plain_result("\n".join(lines))

    def _list_available_tokens(self):
        """列出 tokens 目录下所有 .json 文件（排除内部文件）"""
        if not os.path.exists(self.tokens_dir):
            return []
        return sorted(
            f for f in os.listdir(self.tokens_dir)
            if f.endswith('.json') and f.startswith('token_')
        )

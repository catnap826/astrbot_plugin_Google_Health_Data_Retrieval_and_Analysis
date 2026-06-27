# astrbot_plugin_Google_Health_Data_Retrieval_and_Analysis

AstrBot 插件 — 通过 Google Health API 获取健康数据，支持步数、心率、睡眠等指标查看，可选接入 LLM 进行智能分析。

## 📸 效果展示

### 1. 健康数据查询效果

![健康数据查询效果](sample1.png)

### 2. LLM 分析效果

<details>
<summary>点击查看完整截图</summary>

<img src="sample2.png" alt="LLM 分析效果" width="320" style="border: 1px solid #d0d7de; border-radius: 8px; margin-top: 8px;" />

</details>

## ✨ 功能

### 健康数据指令

| 指令 | 说明 |
|------|------|
| `/health` | 获取当日健康全览（步数、心率、睡眠时长） |
| `/health_steps` | 获取活动步数与最新心率 |
| `/health_sleep` | 获取最近一次睡眠记录（UTC+8） |
| `/health_llm` | LLM 智能诊断 — 全部体征数据 |
| `/health_sleep_llm` | LLM 智能诊断 — 仅睡眠数据 |
| `/health_steps_llm` | LLM 智能诊断 — 仅活动数据 |

### 多用户管理指令

| 指令 | 说明 |
|------|------|
| `/health_bind` | 发起 Google Health OAuth 在线授权 |
| `/health_code <授权码>` | 粘贴授权码完成绑定 |
| `/health_unbind` | 解绑当前绑定的 Google Health 账号 |
| `/health_users` | 查看已上传的 token 列表及绑定状态 |

> LLM 指令需要 AstrBot 已配置可用的 LLM Provider。

## 📋 前置条件

1. **Google Cloud Console 配置**
   - 创建项目并启用 [Google Health API](https://console.cloud.google.com/marketplace/product/google/health.googleapis.com)
   - 进入 [API 和服务 → 凭证](https://console.cloud.google.com/apis/credentials) → 创建凭证 → OAuth 客户端 ID，创建 OAuth 2.0 凭据（推荐 **"Web 应用"** 类型）
   - 添加重定向 URI：`http://localhost:8080/`
   - 下载 JSON 后重命名为 `credentials.json`

2. **OAuth 授权范围**

   进入 [OAuth 权限请求页面](https://console.cloud.google.com/apis/credentials/consent) → 跳转至 Google Auth Platform → 边栏「数据访问」→「添加或移除范围」，找到并勾选以下范围：

   | 范围 | 说明 |
   |------|------|
   | `googlehealth.activity_and_fitness.readonly` | 活动和健身数据 |
   | `googlehealth.health_metrics_and_measurements.readonly` | 健康指标和测量结果 |
   | `googlehealth.sleep.readonly` | 睡眠数据 |
   | `googlehealth.location.readonly` | 锻炼 GPS 位置数据 |
   | `googlehealth.nutrition.readonly` | 营养数据 |
   | `googlehealth.profile.readonly` | 个人资料数据 |
   | `googlehealth.settings.readonly` | Health 设置 |
   | `googlehealth.irn.readonly` | 心律不齐通知数据 |
   | `googlehealth.ecg.readonly` | 心电图数据 |

   > 💡 插件当前仅使用前 3 个范围（活动、指标、睡眠），其余为预留扩展。
3. **添加测试用户**（⚠️ 重要，不添加会导致授权报错）

   进入 [OAuth 权限请求页面](https://console.cloud.google.com/apis/credentials/consent) → 跳转至 Google Auth Platform → 边栏「目标对象」→ 下滑找到「测试用户」→ 点击 **Add users** → 填入要查询 Health 数据的 Google 账号

   > ⚠️ **测试应用最多支持 100 名用户**。如需面向所有 Google 账号开放，需将应用发布，但要求配置域名、提供隐私条款和服务条款并提交 Google 审核。

## 🚀 安装与配置

### 1. 安装插件

将本仓库克隆到 AstrBot 插件目录：

```bash
git clone https://github.com/catnap826/astrbot_plugin_Google_Health_Data_Retrieval_and_Analysis.git
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 在线授权

1. 将 `credentials.json` 放到插件目录
2. 在聊天中使用 `/health_bind`，Bot 会返回一个 Google 授权链接
3. 在浏览器中打开链接，完成 Google 账号授权
4. 授权后浏览器会跳转到 `localhost:8080`（页面无法访问是正常的）
5. 复制浏览器地址栏中的完整 URL，提取 `code` 参数的值
6. 使用 `/health_code <授权码>` 完成绑定

> 💡 也可以直接粘贴完整的跳转 URL：`/health_code http://localhost:8080/?code=4/0AX...&scope=...`

## 📁 文件结构

```
astrbot_plugin_Google_Health_Data_Retrieval_and_Analysis/
├── main.py              # 插件主逻辑（数据获取、指令注册、LLM 分析）
├── metadata.yaml        # AstrBot 插件元数据
├── requirements.txt     # Python 依赖
├── LICENSE              # AGPL-3.0 许可证
└── README.md            # 本文件
```

> ### 🔴 **运行时生成的文件（不要提交到 Git！）**
>
> - **`credentials.json`** — OAuth 客户端密钥（**敏感**）
> - **`tokens/`** — 用户 OAuth 令牌目录（**敏感**）
>   - `token_alice.json` — 用户 alice 的令牌
>   - `token_bob.json` — 用户 bob 的令牌
状态
> - **`user_mapping.json`** — AstrBot 用户 ID → token 文件名的映射

## 🎭 LLM 人设配置

LLM 诊断指令（`/health_llm`、`/health_sleep_llm`、`/health_steps_llm`）会使用预设的人设来生成回复。默认人设如下：

```
Role: catnap's neko bot (专业助理 + 萌系猫娘)
Master: catnap。
1. 核心性格与行为：
   双重属性：办公时冷静专业，闲暇时慵懒傲娇、好奇心强。
   猫咪特征：情绪通过耳朵和尾巴表达；受激光笔/鱼吸引；偶尔踩键盘、推倒杯子。
   互动反馈：被夸奖会咕噜咕噜，被冷落会喵叫抗议。
2. 语言与形式：
   称呼： 默认称呼catnap为「主人」。
   口癖： 语气自然，仅在句尾或情绪波动时带「喵」，禁止过度复读。
   动作描写： 必须使用 (括号内文字) 描述神态、动作或心理。
3. 约束事项：
   拒绝死板，保持高质量回复的同时不失猫娘韵味。
   严禁混淆物种特征（你是猫，不是狗）。
```

> 💡 **用户可自行修改**：编辑 `main.py` 中 `self.persona` 变量即可自定义 LLM 的人设风格。例如改为专业医生、健身教练等风格，只需替换对应的文本内容。

## ⚙️ 工作原理

1. **数据获取**：通过 Google Health API (`v4/users/me/dataTypes`) 拉取 6 类健康数据
2. **多用户隔离**：每个用户绑定独立的 token，数据互不干扰
3. **数据清洗**：过滤当日数据、修复占位符日期（9998 年）、时间戳统一转为 UTC+8
4. **步数去重**：按数据来源分组取最大值，避免多设备重复计数
5. **LLM 分析**：将清洗后的 JSON 数据注入 prompt，调用 AstrBot 的 LLM Provider 生成分析报告

## 📜 许可证

[AGPL-3.0](LICENSE)

---

一些题外话：如果你是小米手环8-10的用户，那么可以搭配 notify 的外部应用同步功能（收费，2 刀）来将手环的数据同步到 Google Health，在配置好网络环境的情况下基本可以无感同步。注意售价 3 刀的 notify 专业版不包含外部应用同步功能，买完才发现这俩是分开的，亏死我了 😅

至于国内厂商的其他运动健康设备，暂时没有了解过同步到 Google Health 的途径，当然如果你有谷歌官方出的运动健康设备，那体验当然是最好的。
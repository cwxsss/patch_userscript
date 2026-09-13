# 自建验证码识别后端

给油猴脚本《哈士奇的网页验证码识别工具》用的**自建服务端**。

## 为什么需要它

原脚本（GreasyFork `scripts/440083`，MIT 协议）本身开源，但**服务端不开源**：
它把验证码图片、页面 URL、设备指纹等数据发往作者私服 `http://115.191.58.84:7070`
（明文 HTTP，每 IP 每天限 50 次）。GitHub / 互联网上不存在该服务端的公开源码。

这份代码是按脚本的**真实 API 契约**逆向实现的一套兼容后端，
把数据收回到你自己的服务器上。所有接口字段都从脚本源码逐个反推验证过。

---

## 一、快速开始

### 方式 A：本地直接跑（Windows / Linux / macOS）

```bash
cd captcha-server

# 1) 建虚拟环境并装依赖（ddddocr 约 100MB，含 onnxruntime）
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt      # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # Linux/macOS

# 2) 自检：不启服务，直接验证 OCR / 滑块 / 存储三条链路
.venv/Scripts/python tools/selftest.py

# 3) 启动
.venv/Scripts/python run.py
# 或者：uvicorn app.main:app --app-dir . --host 0.0.0.0 --port 7070
```

浏览器打开 `http://127.0.0.1:7070/health`，看到 `"status":"up"` 即成功。

### 方式 B：Docker（推荐，环境最省心）

```bash
cd captcha-server
docker compose up -d --build
docker compose logs -f
```

`Dockerfile` 基于 `python:3.11-slim`（已装好 opencv 所需系统库），
实体机 Python 3.10–3.13 均可，无需自己折腾依赖。

### 方式 C：验证接口（可选）

```bash
.venv/Scripts/python tools/http_test.py http://127.0.0.1:7070
```
会按油猴脚本的请求方式打一遍全部 22 个接口，校验响应信封是否合规。

---

## 二、让脚本指向你的服务器

脚本里后端地址是一个硬编码常量：`var og="http://115.191.58.84:7070"`。
用自带工具一键改写：

```bash
python tools/patch_userscript.py 原始脚本路径 -o dist/自建版.user.js \
    --base http://192.168.1.10:7070
```

- `--base` 填你的服务地址。**局域网自用可填内网 IP；要公网用建议套 Nginx + HTTPS。**
- 生成的文件在 Tampermonkey 里导入，**先禁用/卸载原脚本**，避免两个同时跑。
- 工具会自动给脚本头部补一条 `@connect <你的域名>`。

> 脚本是 MIT 协议，改造自用完全合规。

---

## 三、接口契约（逆向自脚本 v7.95）

### 响应信封（所有接口统一）

```jsonc
// 成功
{ "ok": true, "data": { ... } }
// 失败
{ "ok": false, "error": { "code": "XXX", "message": "给人看的说明" } }
```

脚本侧会用 `ok===true && data.value` 之类的判断，**字段名必须是这些**，否则会报
`Unknown response structure`。注意即使业务失败也要用 **HTTP 200** 返回 `ok:false`
（只有额度码接口例外，脚本靠 404/405 判断"服务端不支持"）。

### 接口清单

| 方法 | 路径 | 作用 | 关键响应字段 |
|---|---|---|---|
| POST | `/captcha` | 普通验证码识别 | `data.value`（**字符串，必需**）、`confidence` |
| POST | `/captcha-feedback` | 识别结果反馈 | `data.accepted` |
| POST | `/api/slide/solve` | 滑块缺口求解 | `data.distance`（**数字，必需**）、`rawDistance`、`confidence` |
| GET | `/api/slide/trajectory/config` | 滑块轨迹模板下发 | `templates`、`config_version` |
| POST | `/api/slide/trajectory/report` | 轨迹上报 | `data.accepted` |
| POST | `/api/slide/human-sample/report` | 人工拖动样本上报 | `data.saved` |
| POST | `/api/normal/shared-binding/report\|event` | 普通验证码共享绑定 | `data.accepted` |
| POST | `/api/slide/shared-binding/report\|event` | 滑块共享绑定 | `data.accepted` |
| GET | `/api/slide/shared-binding` | 查询共享绑定 | `status`、`conflict`、`items` |
| POST | `/api/slide/sensitive-limit/check\|success` | 敏感站点限流 | `blocked` |
| GET | `/api/userscript/config` | 通用远程配置 | `features`/`rules`/`interactionNotices` 必须是对象 |
| GET | `/api/userscript/page-config` | 页面级配置 | `cache_ttl_seconds`、`normal_image_denylist` |
| POST | `/api/userscript/events` | 埋点批量上报 | `responses` 必须与 `events` **等长** |
| POST | `/api/userscript/event` | 埋点单条 | `data.accepted` |
| POST | `/api/userscript/identity-sync` | 设备指纹身份同步 | `data.accepted` |
| POST | `/api/userscript/trace-upload` | 链路追踪上报 | `data.accepted` |
| POST | `/api/userscript/suppression-summary` | 抑制汇总 | `data.accepted` |
| POST | `/api/userscript/quota-code/activate` | 额度码激活 | 支持则 200 + `ok:true` |

`/api/test/slide-captcha/solve` 是脚本本地调试用的别名，已一并实现。

### 几个容易踩的坑（已在代码里规避）

1. **`data.value` 必须是字符串**，`data.distance` 必须是数字 —— 类型错了前端直接判失败。
2. **`/api/userscript/events` 的 `responses` 数组长度必须等于事件数**，
   否则前端会把未匹配的事件标记为 `BATCH_NOT_PROCESSED` 并重试。
3. **`/api/userscript/page-config` 不要下发无效的 `slideTrajectory`**，
   一旦字段存在但不合法，前端会判定整个配置无效并丢弃。
4. **`/api/userscript/config` 的 `features` / `rules` / `interactionNotices` 必须是对象**。
5. **错误码 `USERSCRIPT_UPDATE_REQUIRED`** 是脚本的"强制升级"开关，
   返回它会让脚本停止一切请求 —— 自建服务里千万别用。

---

## 四、配置说明（`config.json`）

所有字段都可用环境变量覆盖，格式 `CAPTCHA_<段>__<键>`，例如：

```bash
CAPTCHA_SERVER__PORT=8080
CAPTCHA_AUTH__REQUIRE_API_KEY=true
CAPTCHA_AUTH__API_KEY=your-secret
```

### `auth` —— 鉴权与限流

| 键 | 说明 |
|---|---|
| `require_api_key` | 打开后要求请求头 `X-Api-Key`。**公网部署务必打开** |
| `api_key` | 密钥 |
| `daily_limit_per_ip` | 每 IP 每日识别上限，`0` = 不限（原私服是 50） |

### `ocr` —— 普通验证码

| 键 | 说明 |
|---|---|
| `provider` | `ddddocr`（默认）或 `none`（关闭） |
| `arithmetic` | 自动把 `3+4=?` 算成 `7` |
| `beta_model` | 用 ddddocr beta 模型，对字母/算术更友好，略慢 |
| `allow_remote_image_url` | 是否允许 `image` 传远程 URL。**默认关，防 SSRF** |

### `slider` —— 滑块（需要按站点微调）

| 键 | 说明 |
|---|---|
| `method` | `auto`（默认）/ `gray` 强制灰度法 / `shape` 强制轮廓法 |
| `gray_score_gate` | auto 模式下灰度法得分低于此值就切轮廓法 |
| `suppress_left_ratio` | 抑制左侧初始位置的比例，`null` = 自动（有 alpha 用 1.0） |
| `offset` | 统一像素修正量，**站点对不上时优先调这个** |
| `output_mode` | `display`（换算到背景图展示宽度，默认）/ `raw`（原图像素） |

**滑块算不准怎么办？** 用调试接口看匹配框画在哪：

```bash
curl -X POST http://127.0.0.1:7070/admin/slider-debug \
  -H 'Content-Type: application/json' \
  -d '{"backgroundImage":"<base64>","targetImage":"<base64>","backgroundDisplayWidth":320}' \
  -o debug.png
```

响应头 `X-Distance` / `X-Raw-Distance` / `X-Confidence` 会告诉你识别值和得分。
`tools/slider_experiment.py` 则是对比不同策略命中率的实验脚本。

---

## 五、算法说明（为什么这么写）

滑块求解的核心结论（`tools/slider_experiment.py`，4 个缺口位置 × 2 类背景）：

| 策略 | 平滑渐变背景 | 有纹理背景 |
|---|---|---|
| 灰度 + alpha 掩码互相关（未模糊） | **4/4** | **4/4** |
| 同上但先做高斯模糊 | 0/4 | 4/4 |
| 边缘 + 掩码 | 不可用（0/0 浮点溢出） | 不可用 |
| 拼图块轮廓 vs 背景边缘 | 4/4 | 2/4 |

- **主策略**：灰度图 + alpha 掩码的 `TM_CCORR_NORMED`。因为绝大多数站点的"缺口"
  就是拼图块内容变暗的结果，而归一化互相关对整体亮度缩放不敏感 —— 变暗不影响定位。
- **不要把灰度图先模糊**：平滑背景上会抹掉缺口纹理，产生大量并列峰值选错位置。
  模糊只用在轮廓法的 Canny 上。
- **兜底**：轮廓法（拼图块 mask 对背景边缘做互相关），用于缺口内容与拼图块不一致的站点。

普通验证码用 `ddddocr`（内置 ONNX 神经网络模型），算术验证码读出表达式后本地求值。

---

## 六、目录结构

```
captcha-server/
├── app/
│   ├── main.py            # FastAPI 路由：全部 22 个接口
│   ├── settings.py        # 配置加载（config.json + 环境变量覆盖）
│   ├── protocol.py        # 响应信封 / 客户端 IP
│   ├── ocr_engine.py      # ddddocr 封装 + 算术验证码
│   ├── slider_engine.py   # 滑块缺口求解
│   ├── imageutil.py       # dataURL / base64 解码
│   └── store.py           # SQLite 落库（识别日志/反馈/样本/埋点/绑定）
├── tools/
│   ├── selftest.py            # 离线自检（18 项）
│   ├── http_test.py           # HTTP 端到端测试（33 项）
│   ├── patch_userscript.py    # 改写脚本服务端地址
│   ├── slider_debug.py        # 滑块策略调试
│   └── slider_experiment.py   # 滑块策略对比实验
├── config.json
├── requirements.txt
├── Dockerfile / docker-compose.yml
└── run.py
```

数据默认落在 `data/captcha.db`（SQLite，启用 WAL），表：
`recognition_log` / `feedback` / `slide_sample` / `event` / `binding` / `identity` / `quota_usage`。

管理接口：`GET /admin/stats`（统计与识别成功率）、`POST /admin/reload`（热重载配置）。

---

## 七、安全建议

1. **不要裸奔公网**。至少打开 `require_api_key`，并且用 Nginx 套一层 HTTPS
   —— 脚本支持 `https://` 地址，改址时直接传 HTTPS 域名即可。
2. **别开 `allow_remote_image_url`**，除非你确实要它去抓远程图片（有 SSRF 风险）。
3. 落库的埋点/样本包含页面 URL，属于敏感数据，注意磁盘权限与保留周期。
4. 把 `daily_limit_per_ip` 设成合理值，避免被人当成公共 OCR 接口刷。
5. 本服务只做「图片 → 文字/距离」的识别，**不代填、不代提交**，
   也不会读取任何 Cookie —— 这正是相比原私服的隐私优势所在。

---

## 八、已知限制

- **滑块不是万能的**：合成样本 100% 命中不代表真实站点 100% 命中。
  不同站点缺口/拼图块的生成方式差异很大，请用 `/admin/slider-debug` 按站点调
  `offset` / `method` / `output_mode`。
- `isSpriteCrop`（精灵图裁剪）场景未做特殊处理，这类站点可能需要额外适配。
- 共享绑定接口只做了落库，未实现跨用户共享逻辑（自建单机没必要，
  脚本会正常退回本地绑定）。
- 轨迹模板下发默认关闭（`dispatch_enabled: false`），
  即滑块由脚本内置的拖动逻辑驱动，服务端只负责算距离。

---

## 九、合规提醒

自动破解验证码会绕过网站的人机校验，可能违反目标站点的用户协议；
在政务、金融、票务等场景使用还可能触及法律。**请仅用于自己的测试环境或已获授权的场景。**
原脚本作者自己也内置了 `.gov/.edu/.mil` 等敏感站点的限流策略，原因就在此。

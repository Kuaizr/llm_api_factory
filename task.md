# 角色设定
你是一个经验丰富的高级全栈工程师，精通 Python、FastAPI、SQLAlchemy、React (TypeScript + Tailwind) 以及大模型 API 网关的设计与开发。

# 任务背景
我们需要对 `LLM API Factory` 这个大模型 API 聚合分发与监控服务进行一次核心架构重构。目标是提升代码的可维护性，并使其完美兼容各大 LLM 工具（如 Claude Code、Codex、Gemini CLI 等）。

# 核心需求
请帮我完成以下 5 个模块的修改与重构：

## 1. 代码解耦
- **后端**：目前 `router.py` 文件过于庞大。请将其按照功能模块拆解（如 `routes/openai_proxy.py`, `routes/anthropic_proxy.py`, `routes/admin.py` 等），并提取公共逻辑。
- **前端**：目前的路由配置和仪表盘相关的 `.tsx` 文件太大。请将臃肿的组件拆分为细粒度的子组件（如拆分出独立的规则组配置面板、API Key 管理组件），并将状态管理逻辑抽离为自定义 Hooks。

## 2. 纯粹的透明透传接口 (Standard Endpoints)
- 提供严格符合官方规范的接口路径：
  - OpenAI 接口路由：`http://localhost:port/openai/v1/*`
  - Anthropic 接口路由：`http://localhost:port/anthropic/v1/*`
- **极简透传**：不要在后端自行定义 Pydantic 模型去解析具体的 Chat/Completions 载荷（以防官方字段更新导致报错）。只需解析请求头、提取我们的 API Key 进行路由鉴权，然后通过 `httpx` 将原始 JSON body 和流式请求原封不动地透传给下游模型节点。
- 让项目仅依赖官方 API 格式，做到以后只需 `pip update openai/anthropic` 或直接发起 HTTP 请求即可使用新特性。

## 3. 基于 API Key 的分组路由设计 (重构核心)
- 废弃之前通过在请求体注入 `extra_body` 来指定路由规则的做法，因为这破坏了官方协议。
- **新逻辑**：将路由规则（Rule Group）与 API Key 强绑定。
  - 一个分组（Group）可以生成并管理多个不同的 API Key。
  - 当系统接收到请求时，通过提取请求头中的 API Key，查询数据库找到其绑定的分组，随后完全按照该分组的规则（如轮询、熔断等）进行请求转发。
- **前端支持**：系统需要默认初始化一个 "default" 分组。在前端的 API Key 管理界面，需要改为“以分组为维度管理其下的 API Key”。

## 4. 鉴权头兼容
- 在接收请求时，网关层的鉴权中间件或依赖注入需同时支持提取 `Authorization: Bearer <key>` (OpenAI 风格) 和 `x-api-key: <key>` (Anthropic 风格)，以获取对应的 Factory API Key。

## 5. 对话记录截获与持久化 (Dump 开关)
- 在前端的规则分组界面增加一个开关：`Dump Chat Records`（是否记录对话日志）。
- 当该分组开启此开关时，途经此分组的所有请求（包括普通的 Chat、Coding 生成、思维链等）的完整 prompt 和 response 都需要被保存。
- **技术约束**：
  - 必须妥善处理流式 (Streaming) 响应：在透传 SSE 数据块给客户端的同时，在后端内存中静默拼接完整的回复文本。
  - I/O 优化：使用 FastAPI 的 `BackgroundTasks` 或异步队列将拼接好的对话记录写入数据库，绝不能阻塞当前请求的高并发返回。

## 6. 增补需求（2026-03-16）
- [x] 标准端点行为核对：确保可以按 OpenAI/Anthropic 官方路径使用（`/openai/v1/*`、`/anthropic/v1/*`），并澄清与 `/v1/*` 的关系。
- [x] 路由规则访问 Key 管理：访问 Key 列表支持随时复制，不仅限于首次创建时展示。
- [x] 探针间隔配置：支持设置为 `-1` 表示禁用自动探针（保留手动探测）。
- [x] 控制台视觉优化：去掉标题中的 `v2.0-Probe`；支持 Dark/Light 主题切换。
- [x] 系统安全：修复管理员密码更新功能，前后端完整可用。
- [x] Dump 会话追踪：改进同会话上下文追踪能力，避免每次都被识别为全新会话。

## 7. 通用 Provider 扩展支持（2026-03-17）
### 7.1 自定义请求配置
- [x] Endpoint 增加扩展字段：
  - `url_path_suffix`: 自定义 URL 后缀路径（如 `/api/chat`、`/v2/generate`）
  - `extra_headers`: JSON 格式的额外请求头（如 `{"X-Custom-Header": "value"}`）
  - `extra_cookies`: Cookie 字符串（如 `session_id=abc123; token=xyz`）
  - `extra_query_params`: JSON 格式的额外 URL 查询参数（如 `{"api_key": "xxx"}`）
- [x] 用途：支持非标准 API 格式的第三方 Provider（如一些国内中转服务、自建服务）

### 7.2 OAuth 认证支持
- [x] Endpoint 增加 OAuth 配置字段：
  - `oauth_config`: JSON 格式存储 OAuth Client Credentials 配置
    ```json
    {
      "token_url": "https://auth.example.com/oauth/token",
      "client_id": "xxx",
      "client_secret": "xxx",
      "scope": "api"
    }
    ```
- [x] 实现 Token 自动获取与刷新逻辑
- [x] Token 缓存机制（Redis 存储）
- [x] 401 自动重试：遇到上游 401 时强制刷新 Token 后重试一次

### 7.3 其他扩展
- [x] Provider 类型扩展：增加 `custom` 类型，允许完全自定义请求模板
  - 前端支持 `openai`、`anthropic`、`custom` 三种 provider 选项
  - OpenAI/Anthropic 标准透传路由自动包含 `custom` 端点作为候选
  - 管理接口对 provider 进行归一化校验，拒绝不支持的值
- [x] 请求体模板：支持变量替换（如 `{{model}}`、`{{prompt}}`）
  - Endpoint 增加 `request_body_template` 字段（TEXT 类型）
  - 支持从原始请求体提取变量：`{{model}}`、`{{prompt}}`（从 messages 或 prompt 字段提取）、其他任意字段
  - 模板渲染结果必须是合法 JSON，否则回退到原始请求体
  - 模板中 `"{{variable}}"` 占位符自动 JSON 编码，支持嵌套对象与数组

## 8. 已知问题与优化
- [x] 旧 `/v1/*` 接口已完全移除，统一使用 `/openai/v1/{path}` 与 `/anthropic/v1/{path}` 标准入口

## 9. API Key 分组双向同步（2026-03-18）
### 9.1 需求说明
- API Key 管理界面支持为 Key 分配多个 Rule Group（多选 Checkbox），其中 `default` 分组为必选
- 路由规则编辑界面支持选择多个 API Key 作为目标
- 两处的选择状态需双向同步：任一端变更，另一端自动更新

### 9.2 实现要点
- 后端 `APIKey` 模型新增 `rule_groups_json` 字段存储多分组，并保留 `rule_group` 兼容旧数据
- 后端 `_sync_rule_targets_for_api_key` 负责 Key 变更时同步到 `RoutingRule.target_key_ids_json`
- 后端 `_sync_api_key_groups_for_rule_targets` 负责 Rule 变更时同步到 `APIKey.rule_groups_json`
- 前端 Key 编辑弹窗提供资格校验接口，未探测模型时自动触发探测

### 9.3 完成状态
- [x] 数据模型扩展与 `assign_rule_groups` 逻辑修正
- [x] Key 界面分组变更 -> 同步到路由规则（正向）
- [x] 规则界面 Key 选择变更 -> 同步到 API Key 分组（反向）
- [x] 双向同步回归测试覆盖（`test_update_rule_target_keys_syncs_api_key_rule_groups`）
- [x] 前端 Key 编辑多选 UI 与资格校验集成


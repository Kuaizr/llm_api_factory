# LLM API 集成框架

我只是想优先使用免费的API

## 项目概述
一个轻量级、可扩展的多平台 LLM API 集成框架，提供统一的接口调用不同大模型平台 API。主要特性包括：

- 多平台 API 统一调用（OpenAI / MoDa / SiliconFlow / FreeAitools / Infini 等）
- 工具调用（Tool Calling）原生透传与处理
- 同步与异步接口（call_api/stream_api + call_api_async/stream_api_async）
- 流式响应处理
- 对话历史管理与上下文窗口控制（trim/summarize）
- 智能路由与负载均衡（failover / round_robin）
- 监控与日志记录
- 抽象化客户端架构（OpenAI SDK 和 通用 HTTP 两大基类）

## 安装与配置

### 从源码安装
```bash
git clone https://github.com/Kuaizr/llm_api_factory.git
cd llm_api_factory
pip install .
```

### 直接从Git安装
```bash
pip install git+https://github.com/Kuaizr/llm_api_factory.git
```

## 核心模块

### `clients/` - 平台客户端实现
- `base_openai_client.py`: 基于 OpenAI SDK 的通用 Chat Completions 客户端基类
- `base_requests_client.py`: 基于 requests/aiohttp 的通用 Chat Completions 客户端基类
- `openai_client.py`: OpenAI 平台（继承 base_openai_client）
- `moda_client.py`: MoDa（继承 base_openai_client）
- `siliconflow_client.py`: SiliconFlow（继承 base_requests_client）
- `free_aitools_client.py`: FreeAitools（继承 base_requests_client）
- `infini_client.py`: Infini（继承 base_requests_client）

### `core/` - 框架核心
- `factory.py`: 客户端工厂类
- `session.py`: 统一会话接口
- `router.py`: API客户端路由
- `executor.py`: API执行器
- `conversation.py`: 对话管理

### `utils/` - 工具类
- `config_manager.py`: 配置管理
- `logger.py`: 日志记录
- `monitor.py`: 性能监控

## 使用示例

### 基本API调用
```python
from llm_api_framework.core import LLMSession

# 初始化会话（从配置读取平台、路由与上下文策略）
session = LLMSession("configs/example.json")

# 同步调用
response = session.call_api("请解释量子计算的基本原理")
print(response.get("content"))

# 流式调用
for chunk in session.stream_api("用简单语言解释相对论"):
    if chunk.get("content"):
        print(chunk.get("content"), end="", flush=True)
```

### 视觉功能示例
```python
# 添加视觉消息
image_url = "https://example.com/image.jpg"
session.add_vision_message("user", [image_url])

# 询问图片内容
response = session.call_api("描述这幅图片")
print(response.get("content"))
```

### 工具调用（Tool Calling）
```python
tools = [{
    "type": "function",
    "function": {
        "name": "lookup_weather",
        "description": "查询天气",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}}
        }
    }
}]

message = session.call_api(
    "上海天气如何？",
    tools=tools,
    tool_choice={"type": "function", "function": {"name": "lookup_weather"}}
)

# 如返回 tool_calls，执行工具并回填 tool 响应
for tc in message.get("tool_calls", []):
    tool_id = tc["id"]
    args = tc["function"]["arguments"]
    result = "...你的工具结果..."
    session.conversation.add_tool_response(tool_id, result)
```

### 异步接口（高并发）
```python
import asyncio
from llm_api_framework.core import LLMSession

async def main():
    session = LLMSession("configs/example.json")
    msg = await session.call_api_async("异步请求示例")
    print(msg.get("content"))

    async for ch in session.stream_api_async("异步流式示例"):
        if ch.get("content"):
            print(ch["content"], end="", flush=True)

asyncio.run(main())
```

## 上下文窗口管理
- 通过 `ConversationManager` 在 `add_message` 时自动控制 Token 预算。
- 策略：
  - `trim`: 保留 system 提示，超限时删除最旧消息（默认）
  - `summarize`: 保留最近 n 条，其余用“当前模型”自动总结为一条系统摘要
- 配置（`configs/example.json`）：
```json
"context": {
  "max_tokens": 8192,
  "overflow_strategy": "summarize",
  "reserve_recent_n": 1,
  "tokenizer_model": "gpt-4o-mini"
}
```
- 代码动态设置：
```python
session.conversation.set_overflow_strategy("summarize", reserve_recent_n=1)
session.conversation.set_max_context_tokens(8192)
```
- 视觉消息 token 计数策略：
  - 图片内容只按“张数”计费，不再基于 base64 文本长度估算
  - 每张图片固定计 **1024 tokens**（参考多数厂商的收费模型）
  - 文本部分仍使用 tiktoken（或字符估算）统计实际 tokens
  - 该策略既避免 base64 爆量导致的超限，也让 token 预算更接近真实调用成本

## 路由与负载均衡
- 支持 `failover`（默认）与 `round_robin`。
- 配置：
```json
"routing": { "strategy": "round_robin" }
```
- 行为：
  - `failover`: 总是使用当前 client，错误时切换。
  - `round_robin`: 每次请求轮询下一个 client。

## 客户端扩展（继承基类）
- OpenAI SDK 系（新增平台大多兼容 OpenAI 生态 API）：
  - 继承 `BaseOpenAIChatCompletionsClient`
  - 实现 `get_default_url()`，在构造函数设置 `self.model`（可选）
- 通用 HTTP 系（OpenAI 格式兼容 /chat/completions 的第三方网关）：
  - 继承 `BaseRequestsChatCompletionsClient`
  - 实现 `get_default_url()`，在构造函数传入 `model`
- 注册到 `PlatformFactory` 的 `_client_classes` 或通过 `register_platform`

## 配置示例（节选）
```json
{
  "system_prompt": "你是一个有帮助的AI助手",
  "platform_order": ["free_aitools", "openai"],
  "routing": { "strategy": "round_robin" },
  "context": {
    "max_tokens": 8192,
    "overflow_strategy": "summarize",
    "reserve_recent_n": 1,
    "tokenizer_model": "gpt-4o-mini"
  },
  "platforms": {
    "free_aitools": {
      "model": "Qwen2.5-7B-Instruct",
      "api_keys": ["your_key"],
      "base_url": "https://platform.aitools.cfd/api/v1"
    },
    "openai": {
      "model": "gpt-3.5-turbo",
      "api_keys": ["sk-..."],
      "base_url": "https://api.openai.com/v1"
    }
  }
}
```

## TODO 列表
- [x] 支持推理模型输出
- [x] 添加视觉输入处理功能
- [x] 增加更多平台支持（MoDa/SiliconFlow/FreeAitools/Infini等）
- [x] 实现 API 调用负载均衡（failover/round_robin）
- [x] 支持工具调用（Tool Calling）
- [x] 提供完整异步接口
- [x] 上下文窗口管理（trim/summarize）

## 贡献指南
欢迎提交Pull Request，请确保：
1. 通过所有单元测试
2. 更新相关文档
3. 遵循代码风格规范
4. 为新功能添加测试用例

## 许可证
MIT License
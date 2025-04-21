# LLM API 集成框架

我只是想优先使用免费的API

## 项目概述
一个轻量级、可扩展的多平台LLM API集成框架，提供统一的接口调用不同大模型平台API。主要特性包括：

- 多平台API统一调用（OpenAI/MoDa/SiliconFlow等）
- 同步/异步接口支持
- 流式响应处理
- 对话历史管理
- 性能监控和日志记录
- 可扩展的客户端架构

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
- `openai_client.py`: OpenAI API客户端
- `moda_client.py`: 魔搭社区的免费API客户端
- `siliconflow_client.py`: SiliconFlow平台客户端

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

# 初始化会话
session = LLMSession("configs/qwen2.5-VL-7B-Instruct.json")

# 同步调用
response = session.call_api("请解释量子计算的基本原理")
print(response["content"])

# 流式调用
for chunk in session.stream_api("用简单语言解释相对论"):
    if chunk["content"]:
        print(chunk["content"], end="", flush=True)
```

### 视觉功能示例
```python
# 添加视觉消息
image_url = "https://example.com/image.jpg"
session.add_vision_message("user", [image_url])

# 询问图片内容
response = session.call_api("描述这幅图片")
print(response["content"])
```

## TODO 列表
- [x] 支持推理模型输出
- [x] 添加视觉输入处理功能
- [ ] 增加更多平台支持（DeepSeek等）
- [ ] 实现API调用负载均衡

## 贡献指南
欢迎提交Pull Request，请确保：
1. 通过所有单元测试
2. 更新相关文档
3. 遵循代码风格规范
4. 为新功能添加测试用例

## 许可证
MIT License
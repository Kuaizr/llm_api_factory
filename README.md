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

### 依赖安装
```bash
pip install -r requirements.txt
```

## 核心模块

### `clients/` - 平台客户端实现
- `openai_client.py`: OpenAI API客户端
- `moda_client.py`: 魔搭社区的免费API客户端
- `siliconflow_client.py`: SiliconFlow平台客户端

### `core/` - 框架核心
- `factory.py`: 客户端工厂类
- `manager.py`: 对话历史管理
- `client.py`: 基础客户端接口

### `utils/` - 工具类
- `config_manager.py`: 配置管理
- `logger.py`: 日志记录
- `monitor.py`: 性能监控

## TODO 列表
### 近期计划
- [ ] 支持推理模型输出（需修改manager API）
- [ ] 添加视觉输入处理功能
- [ ] 增加更多平台支持（DeepSeek等）
- [ ] 实现API调用负载均衡

### 长期规划
- [ ] 添加插件系统扩展功能
- [ ] 支持本地模型部署
- [ ] 开发Web交互界面
- [ ] 实现多模态输入输出

## 贡献指南
欢迎提交Pull Request，请确保：
1. 通过所有单元测试
2. 更新相关文档
3. 遵循代码风格规范
4. 为新功能添加测试用例

## 许可证
MIT License
# LLM API 集成框架

## 项目概述
一个多平台LLM API集成框架，支持：
- 多平台API统一调用（OpenAI/MoDa/SiliconFlow等）
- 同步/异步接口
- 流式响应处理
- 对话历史管理
- 性能监控和日志记录

## 快速开始
1. 安装依赖：
```bash
pip install -r requirements.txt
```

2. 配置API密钥：
复制`configs/example.json`为`configs/default.json`并填写您的API密钥

3. 运行测试：
```bash
python example_usage.py
```

## 功能特性
- 工厂模式管理不同平台客户端
- 自动错误处理和重试机制
- 对话上下文维护
- 性能指标监控（延迟、成功率等）

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

## 项目结构
```
llm_api_framework/
├── clients/        # 各平台客户端实现
├── core/           # 核心框架代码
├── utils/          # 工具类
configs/            # 配置文件目录
example_usage.py    # 使用示例
```

## 贡献指南
欢迎提交Pull Request，请确保：
1. 通过所有单元测试
2. 更新相关文档
3. 遵循代码风格规范
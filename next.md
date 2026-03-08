LLM API Factory v2.0 - 开发与架构文档
1. 项目愿景
构建一个高可用、可观测、可管理的 LLM API 聚合网关。系统采用“VPS探针”式的可视化管理界面，支持多协议接入、精细化路由、流量控制及跨境 Agent 代理。
2. 差距分析 (Gap Analysis)
功能模块	当前状态 (v1)	目标状态 (v2)	缺口 (Gap)
数据模型	简单的 Endpoint/Key 结构	包含限流、配额、策略的复杂实体	缺少 rpm_limit, daily_limit, strategy, provider 等字段
路由机制	静态 ModelMap 映射	动态正则匹配 + 规则组	缺少 RoutingRule 表及正则匹配引擎
权限管理	单一 Token 校验	读写分离 (Public/Admin)	缺少登录接口及访客模式
观测性	仅请求流水日志	聚合图表 + 实时探针	缺少 UsageStats 聚合表及前端图表组件
代理网络	基础 WebSocket 通道	节点版本/延迟管理 + 动态切换	缺少 Agent 元数据管理及前端选择器
3. 数据库设计 (Schema Design)
3.1 Endpoints (端点表)
承载 API 提供商的基础连接信息。
CREATE TABLE endpoints (
    id SERIAL PRIMARY KEY,
    name VARCHAR(128) UNIQUE NOT NULL,
    base_url VARCHAR(512) NOT NULL,
    provider VARCHAR(32) DEFAULT 'openai', -- 'openai' | 'anthropic'
    strategy VARCHAR(32) DEFAULT 'weighted_round_robin', -- 'weighted_round_robin' | 'sequential'
    agent_node VARCHAR(128) NULL, -- 绑定的 Agent 名称
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);
3.2 API Keys (密钥/管道表)
承载具体的认证信息及负载限制。
CREATE TABLE api_keys (
    id SERIAL PRIMARY KEY,
    endpoint_id INTEGER REFERENCES endpoints(id),
    name VARCHAR(64), -- 备注，如 "Free Tier Account 1"
    key TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    
    -- 流量控制
    rpm_limit INTEGER NULL, -- 速率限制
    daily_limit INTEGER NULL, -- 每日配额
    
    -- 用量缓存
    used_today INTEGER DEFAULT 0,
    total_usage INTEGER DEFAULT 0,
    
    created_at TIMESTAMP DEFAULT NOW()
);
3.3 Routing Rules (路由规则表)
定义流量的分发逻辑。
CREATE TABLE routing_rules (
    id SERIAL PRIMARY KEY,
    model_pattern VARCHAR(128) NOT NULL, -- 正则表达式，如 "gpt-4.*"
    group_name VARCHAR(64) DEFAULT 'default',
    priority INTEGER DEFAULT 10,
    is_active BOOLEAN DEFAULT TRUE,
    target_key_ids_json TEXT NOT NULL, -- JSON Array: "[1, 2, 5]"
    created_at TIMESTAMP DEFAULT NOW()
);
4. API 接口规范
4.1 认证 (Authentication)
* POST /auth/login: 用户登录，返回 JWT Token。
    * Payload: { "password": "..." }
* GET /auth/me: 验证当前 Token 有效性。
4.2 公开数据 (Public Dashboard)
* GET /v1/status/dashboard: 获取系统概览（端点状态、Agent 状态），不包含 Key 明文。
4.3 管理接口 (Admin) - Header: Authorization: Bearer <token>
* Endpoints
    * GET /admin/endpoints: 获取完整列表。
    * POST /admin/endpoints
    * PUT /admin/endpoints/{id}
    * POST /admin/endpoints/{id}/probe: 触发模型自动发现。
* Keys
    * POST /admin/endpoints/{id}/keys
    * PUT /admin/keys/{id}
* Rules
    * GET /admin/rules
    * POST /admin/rules
* Stats
    * GET /admin/stats/usage: 获取图表数据。
5. 核心逻辑伪代码
5.1 路由选择算法
def select_best_key(user_model, user_group):
    # 1. 查找匹配的规则
    rules = db.query(RoutingRule).filter(group_name=user_group).order_by(priority.desc())
    target_key_ids = []
    
    for rule in rules:
        if re.match(rule.model_pattern, user_model):
            target_key_ids = json.loads(rule.target_key_ids_json)
            break
            
    # 2. 如果没有规则匹配，回退到默认逻辑 (查询所有支持该 Endpoint 的 Key)
    if not target_key_ids:
        # Fallback logic...
        pass
        
    # 3. 过滤熔断器和限流
    available_keys = []
    for kid in target_key_ids:
        if circuit_breaker.is_open(kid): continue
        if rate_limiter.is_limit_exceeded(kid): continue
        available_keys.append(kid)
        
    # 4. 根据 Endpoint 策略选择
    endpoint = available_keys[0].endpoint
    if endpoint.strategy == 'sequential':
        return available_keys[0] # 主备模式，永远选第一个活着的
    else:
        return weighted_round_robin(available_keys)
6. 前端权限逻辑
前端需实现简单的 RBAC（基于角色的访问控制）逻辑：
1. Init: App 启动检查 localStorage 中的 Token。
2. State: const [isAdmin, setIsAdmin] = useState(false)。
3. View:
    * 如果 !isAdmin: 所有 Input disabled, Save/Delete 按钮隐藏或置灰。
    * System Settings 页显示 "Admin Login" 表单。
4. Action:
    * 点击 "Save" -> 检查 Token -> 调用 API。
    * 如果 API 返回 401 -> 弹出登录框 -> setIsAdmin(false)。
7. Telegram 告警配置
在 System Settings 中配置：
* Bot Token
* Chat ID
后端 NotificationService 监听以下事件：
* CircuitBreakerOpen: 当某个 Key 熔断时。
* AgentOffline: 当 Agent 心跳丢失超过阈值时。
* DailyLimitReached: 当 Key 耗尽每日配额时。

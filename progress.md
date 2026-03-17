# 进度总结

## 当前阶段

- 时间：2026-03-16
- 阶段目标：以 `task.md` 为唯一核心任务，继续推进"API Key 分组路由 + default 分组落地 + 回归稳定"。

## ✅ 本轮已完成

### API Key 分组多选功能（2026-03-16 新增）

- **数据模型扩展**：
  - `APIKey` 模型新增 `rule_groups_json` 字段存储多分组，保留 `rule_group` 字段兼容旧数据。
  - 新增 `normalize_rule_groups()` 静态方法统一处理分组规范化（去重、排序、default 必选）。
  - 新增 `rule_groups` 属性解析 JSON 并合并旧字段，`primary_rule_group` 属性返回主要分组。
  - 新增 `assign_rule_groups()` 方法批量设置分组，`in_rule_group()` 方法判断归属。

- **后端跨表同步**：
  - `backend/app/api/v1/route_modules/admin_handlers.py` 实现 `_sync_rule_targets_for_api_key()` 函数：
    - 创建/更新 API Key 时，自动将其 ID 添加到对应分组的 `RoutingRule.target_key_ids_json`。
    - 删除 API Key 时，自动从所有分组的 `target_key_ids_json` 中移除。
    - 事务内保证数据一致性。

- **分组资格校验与自动探测**：
  - 新增 `POST /admin/endpoints/{endpoint_id}/keys/check-rule-group` 接口：
    - 校验 API Key/端点是否满足分组的模型匹配规则。
    - 若无 ModelMap 记录，自动调用 `/v1/models` 探测并持久化。
    - 返回 `eligible`、`probed`、`matched_models`、`required_patterns` 等信息。
  - 新增 `RuleGroupEligibilityCheck` 和 `RuleGroupEligibilityOut` 数据模型。

- **前端交互增强**：
  - Key 管理弹窗分组由下拉单选改为 Checkbox 多选。
  - `default` 分组 Checkbox 默认选中且禁用（必选）。
  - 切换分组 Checkbox 时触发后端资格校验 API，不满足则阻止选中并显示错误提示。
  - 自动探测成功时显示 toast 通知（"分组 xxx 校验前已自动补充模型探测"）。
  - 创建/更新请求体携带 `rule_groups` 数组字段。

- **回归测试补齐**：
  - `backend/tests/test_admin_rules.py` 新增：
    - `test_create_endpoint_key_syncs_multi_rule_group_targets`：验证创建 Key 时多分组目标同步。
    - `test_update_key_resyncs_rule_group_targets`：验证更新 Key 分组时目标列表重新同步。
    - `test_rule_group_eligibility_auto_probes_when_model_maps_missing`：验证无 ModelMap 时自动探测逻辑。
  - `frontend/src/pages/Assets.test.tsx` 新增：
    - `"creates endpoint key with eligible multi rule groups"`：多选 UI + 资格校验通过流程。
    - `"shows error and blocks selecting ineligible rule group"`：资格校验失败阻止选中流程。
    - `"validates empty api key when creating"`：空 Key 校验。

- 任务驱动约束保持：
  - 继续只按 `task.md` 推进，不再维护 `next.md`。
- 鉴权与标准透传（已完成项）保持稳定：
  - `Authorization: Bearer <key>` 与 `x-api-key: <key>` 双头兼容仍可用。
  - `/openai/v1/*` 与 `/anthropic/v1/*` 标准透传路径保持通过。
- API Key 按分组管理（task.md 第 3 项）继续落地：
  - `backend/app/api/v1/route_models.py` 与 `backend/app/api/v1/route_helpers.py` 的 endpoint key 输出补充 `rule_group` 字段。
  - `frontend/src/pages/console/endpoint-modals.tsx` 的 Key 管理弹窗支持：
    - 按分组筛选（全部/指定分组）。
    - 新建/编辑时选择分组并按分组排序展示。
    - `default` 分组显示 `System Group` 标识。
    - 新建时 API Key 必填、Quota/RPM 非负校验。
  - `frontend/src/pages/Console.tsx` 的 Key 创建/更新请求体补充 `rule_group`。
- Dump 开关联调（task.md 第 5 项）补强：
  - `frontend/src/pages/RouterLab.test.tsx` 增加规则编辑后提交 `dump_enabled/dump_path` 的联调断言。
  - `backend/tests/test_admin_rules.py` 增加规则创建/查询时 dump 字段断言。
- 前端解耦（task.md 第 1 项）继续推进：
  - 新增 `frontend/src/pages/console/use-console-data.ts`，承接 Console 的鉴权、列表拉取、趋势与健康状态轮询。
  - 新增 `frontend/src/pages/console/use-console-actions.ts`，抽离端点/规则/Agent/Probe 的操作事件逻辑。
  - 新增 `frontend/src/pages/console/endpoints-panel.tsx`，拆出端点总览统计与 Endpoint 卡片视图。
  - 新增 `frontend/src/pages/console/probe-models-modal.tsx`，拆出模型探测结果弹窗。
  - `frontend/src/pages/Console.tsx` 改为消费 `useConsoleData` + `useConsoleActions` 与新子组件，移除内嵌的大段数据加载/展示/操作逻辑。
- 测试补强与修正：
  - `frontend/src/pages/Assets.test.tsx` 新增“按分组创建 Key”与“空 Key 校验”测试。
  - `backend/tests/test_admin_endpoints_detail.py` 补充 `rule_group` 返回字段断言。

## ✅ 回归结果

- 后端关键回归：
  - `cd backend && pytest -q tests/test_admin_endpoints_detail.py tests/test_admin_rules.py tests/test_auth_routes.py tests/test_openai_proxy_routes.py --maxfail=1` => `28 passed`
- 后端增量校验：
  - `cd backend && pytest -q tests/test_admin_rules.py tests/test_admin_endpoints_detail.py --maxfail=1` => `9 passed`（含多分组同步测试）
- 后端全量回归：
  - `cd backend && pytest -q` => `72 passed`
- 前端关键页面定向：
  - `cd frontend && npm test -- src/pages/Assets.test.tsx` => `5 tests passed`（含多选分组校验测试）
  - 注：仍有 React `act(...)` warning，但不影响用例通过。

## ⏳ 当前任务进度（以 task.md 为准）

| 任务 | 状态 | 优先级 | 备注 |
| --- | --- | --- | --- |
| 1. 代码解耦（后端） | 已完成 | P0 | 路由/处理器拆分与兼容层收敛已完成 |
| 1. 代码解耦（前端） | 进行中 | P1 | 数据加载、操作事件、端点主视图、Probe 弹窗均已拆分；仍可继续拆导航/模态同步逻辑 |
| 2. 标准端点透明透传 | 已完成 | P0 | `/openai/v1/*` 与 `/anthropic/v1/*` 已覆盖并回归通过 |
| 3. API Key 绑定分组路由 | 已完成 | P0 | default 分组自动初始化+保护 + 多选分组 + 跨表同步 + 资格校验已联通 |
| 4. 鉴权头兼容（Bearer + x-api-key） | 已完成 | P0 | 网关鉴权与认证接口均已兼容 |
| 5. Dump Chat Records 开关与持久化 | 已完成（核心） | P0 | 后端流式拼接与异步落库在位，前端开关联调与断言已补齐 |
| 6. 交付整理与分批提交 | 进行中 | P1 | 需继续收敛运行态/日志类噪音改动 |

## 当前阻塞/风险

- 工作区历史改动较多，提交分组仍需谨慎，避免混入无关变更。
- `frontend` 的 `npm test` 默认会扫到 Playwright E2E 文件（`tests/e2e/*.spec.ts`），需使用定向命令或拆分脚本，避免与 Vitest 混跑。
- IDE linter 仍存在环境级 import warning（`fastapi/sqlalchemy/httpx/pytest` 解析），不影响运行与测试结果。

## 下一步建议

1. 继续推进 `task.md` 第 1 项前端解耦：可再拆导航区与弹窗状态同步逻辑，进一步压缩 `Console.tsx`。
2. 收敛前端测试中的 React `act(...)` warning，减少 CI 噪音。
3. 已完成后端全量 + 前端关键回归；下一步聚焦清理日志/构建噪音改动并分批提交。

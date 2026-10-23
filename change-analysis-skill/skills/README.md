# Skill 目录

本项目只提供一个 Skill：[change-analysis](<change-analysis/SKILL.md>)。

它根据代码变更、可用需求与源码证据，分析影响链路、风险和测试范围。当前助手执行最终分析；Skill 提供确定性证据，不调用外部模型服务。

## 使用边界

1. 按 Skill 步骤调用 Skill，保留完整证据及摘要。
2. 阅读 `analysis_input.analysis_guidance`；必要时沿 `evidence_reference` 读取完整证据，检查裁剪、失败和范围限制。
3. 对 diff、候选调用方和前后端契约做源码确认，不把文本命中当成确认调用链。
4. 有实际需求正文时对照验收点；仅 patch 或需求缺失时说明推断和不可确认范围。
5. 按统一模板完成业务判断和可执行测试场景；自动证据报告不是最终分析结果，未执行的测试明确注明。

需求读取、确定性规则扫描、报告渲染和可选钉钉 Bridge 都是 Skill 能力，不单独注册 Skill。内部需求 CLI/包装器只用于采集、排查和测试。

安装、实际支持的命令及环境配置见 [Skill 说明](<../README.md>)；模块边界见 [结构说明](<../docs/structure.md>)。

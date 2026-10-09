# Codex Skills

本目录存放与 Harness 配套的两个 Codex Skill：

- change-analysis/SKILL.md：读取需求和 Git 证据，分析代码变更、影响范围与测试点；
- requirement-document/SKILL.md：只获取并整理 TAPD/钉钉需求文档。

Skill 由 Codex 直接执行分析。Harness 只负责采集文档和代码证据，不连接外部模型能力。

## 安装

将两个目录复制到当前用户的 Codex Skills 目录：

~~~powershell
$Skills = "$env:USERPROFILE/.codex/skills"
New-Item -ItemType Directory -Force "$Skills/change-analysis" | Out-Null
New-Item -ItemType Directory -Force "$Skills/requirement-document" | Out-Null
Copy-Item ./skills/change-analysis/SKILL.md "$Skills/change-analysis/SKILL.md" -Force
Copy-Item ./skills/requirement-document/SKILL.md "$Skills/requirement-document/SKILL.md" -Force
~~~

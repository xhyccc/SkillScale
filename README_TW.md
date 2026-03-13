# SkillScale — 分散式技能即服務代理基礎設施


> [English](README.md) | [简体中文](README_CN.md) | [繁體中文](README_TW.md) | [日本語](README_JP.md) | [Español](README_ES.md) | [Français](README_FR.md)


## 核心原則

### SkillScale 解決什麼問題？

現代 AI Agent 生態系面臨嚴重的**協議碎片化**問題：MCP 客戶端（Claude Desktop、Cursor 等）用一種協議，A2A 代理（Google、企業平台）用另一種協議，技能執行後端又需要不同介面。SkillScale 用**三層架構**解決：

```
  協議層              →  閘道層              →  執行層
  (MCP/A2A 客戶端)      (Rust, 協議轉換)         (Kafka+技能伺服器、技能發現&LLM)
```

**關鍵洞察**：閘道是純粹的**協議翻譯器**，外部說 MCP 和 A2A，內部全部轉成 Kafka 訊息。這意味著：

- 新增協議 = 閘道加一個 HTTP handler
- 新增技能 = `skills/` 新增資料夾並重啟
- 擴展 = 增加技能伺服器容器（Kafka 自動分配）

### 請求流程

```
 客戶端                Rust 閘道                Redpanda            技能伺服器
```

...existing code...

## 授權

MIT

# SkillScale — 分散型スキル・アズ・ア・サービス エージェント基盤


> [English](README.md) | [简体中文](README_CN.md) | [繁體中文](README_TW.md) | [日本語](README_JP.md) | [Español](README_ES.md) | [Français](README_FR.md)


## コア原則

### SkillScaleはどんな問題を解決するのか？

現代のAIエージェントエコシステムは**プロトコルの断片化**問題に直面しています。MCPクライアント（Claude Desktop、Cursorなど）は1つのプロトコルを話し、A2Aエージェント（Google、企業プラットフォーム）は別のプロトコルを話し、実際のスキル実行バックエンドはさらに別のインターフェースを必要とします。SkillScaleは**三層アーキテクチャ**でこれを解決します：

```
  プロトコル層        →  ゲートウェイ層        →  実行層
  (MCP/A2Aクライアント)   (Rust, プロトコル変換)   (Kafka+スキルサーバ、スキル発見&LLM)
```

**重要な洞察**：ゲートウェイは純粋な**プロトコル変換器**です。外部ではMCPとA2Aを話しますが、内部ではすべてKafkaメッセージになります。つまり：

- 新しいプロトコル追加＝ゲートウェイにHTTPハンドラ追加
- 新しいスキル追加＝`skills/`にフォルダを追加して再起動
- スケーリング＝スキルサーバコンテナを追加（Kafkaが分散処理）

### リクエストの流れ

```
 クライアント         Rustゲートウェイ         Redpanda           スキルサーバ
   │                        │                      │                     │
   │── MCP call_tool ────▶│                      │                     │
   │   またはA2A POST      │                      │                     │
   │                        │── Kafka Produce ──▶│                     │
   │                        │   topic: TOPIC_CODE_│                     │
   │                        │   ANALYSIS          │                     │
   │                        │   reply_to: REPLY_xxx│                    │
   │                        │                      │── Kafka Consume ─▶│
   │                        │                      │                     │── AGENTS.md解析
   │                        │                      │                     │── LLMでスキル選択
   │                        │                      │                     │── スキル実行(stdin→stdout)
   │                        │                      │                     │── LLMレビュー（任意）
   │                        │                      │◀── Kafka Produce ─│
   │                        │                      │   topic: REPLY_xxx │
   │                        │◀── Kafka Consume ──│                     │
   │◀── MCP result ──────│                      │                     │
   │    またはA2A response │                      │                     │
```

...existing code...

## ライセンス

MIT

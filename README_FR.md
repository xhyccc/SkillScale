# SkillScale — Infrastructure distribuée d'agent « Skill-as-a-Service »


> [English](README.md) | [简体中文](README_CN.md) | [繁體中文](README_TW.md) | [日本語](README_JP.md) | [Español](README_ES.md) | [Français](README_FR.md)


## Principes fondamentaux

### Quel problème SkillScale résout-il ?

Les écosystèmes modernes d'agents IA font face à un problème de **fragmentation des protocoles** : les clients MCP (Claude Desktop, Cursor, etc.) utilisent un protocole, les agents A2A (Google, plateformes d'entreprise) en utilisent un autre, et les backends d'exécution des compétences nécessitent une interface différente. SkillScale résout cela avec une **architecture à trois couches** :

```
  Couche protocole        →  Couche passerelle        →  Couche exécution
  (Clients MCP/A2A)          (Rust, traduction protocole)   (Kafka + serveurs de compétences, découverte & LLM)
```

**Idée clé** : La passerelle est un pur **traducteur de protocoles** — elle parle MCP et A2A à l'extérieur, mais en interne tout devient un message Kafka. Cela signifie :

- Ajouter un nouveau protocole = ajouter un handler HTTP dans la passerelle
- Ajouter une nouvelle compétence = déposer un dossier dans `skills/` et redémarrer
- Scalabilité = ajouter plus de conteneurs serveurs de compétences (Kafka gère la distribution)

### Flux d'une requête

```
 Client                  Passerelle Rust                Redpanda            Serveur de compétences
   │                          │                        │                     │
   │── MCP call_tool ────────▶│                        │                     │
   │   ou POST A2A            │                        │                     │
   │                          │── Kafka Produce ──────▶│                     │
   │                          │   topic: TOPIC_CODE_   │                     │
   │                          │   ANALYSIS             │                     │
   │                          │   reply_to: REPLY_xxx  │                     │
   │                          │                        │── Kafka Consume ──▶│
   │                          │                        │                     │── analyser AGENTS.md
   │                          │                        │                     │── LLM sélectionne la compétence
   │                          │                        │                     │── exécuter la compétence (stdin→stdout)
   │                          │                        │                     │── revue LLM (optionnel)
   │                          │                        │◀── Kafka Produce ──│
   │                          │                        │   topic: REPLY_xxx  │
   │                          │◀── Kafka Consume ──────│                     │
   │◀── résultat MCP ─────────│                        │                     │
   │    ou réponse A2A        │                        │                     │
```

...existing code...

## Licence

MIT

# SkillScale — Infraestructura distribuida de habilidades como servicio para agentes


> [English](README.md) | [简体中文](README_CN.md) | [繁體中文](README_TW.md) | [日本語](README_JP.md) | [Español](README_ES.md) | [Français](README_FR.md)


## Principios fundamentales

### ¿Qué problema resuelve SkillScale?

Los ecosistemas modernos de agentes de IA enfrentan un problema de **fragmentación de protocolos**: los clientes MCP (Claude Desktop, Cursor, etc.) usan un protocolo, los agentes A2A (Google, plataformas empresariales) usan otro, y los backends de ejecución de habilidades requieren una interfaz diferente. SkillScale lo resuelve con una **arquitectura de tres capas**:

```
  Capa de protocolo      →  Capa de puerta de enlace      →  Capa de ejecución
  (Clientes MCP/A2A)        (Rust, traducción de protocolo)   (Kafka + servidores de habilidades, descubrimiento de habilidades y LLM)
```

**Idea clave**: La puerta de enlace es un puro **traductor de protocolos** — habla MCP y A2A externamente, pero internamente todo se convierte en un mensaje de Kafka. Esto significa:

- Agregar un nuevo protocolo = agregar un manejador HTTP en la puerta de enlace
- Agregar una nueva habilidad = agregar una carpeta en `skills/` y reiniciar
- Escalar = agregar más contenedores de servidores de habilidades (Kafka maneja la distribución)

### Flujo de una solicitud

```
 Cliente                  Puerta de enlace Rust           Redpanda            Servidor de habilidades
   │                          │                        │                     │
   │── MCP call_tool ────────▶│                        │                     │
   │   o POST A2A             │                        │                     │
   │                          │── Kafka Produce ──────▶│                     │
   │                          │   topic: TOPIC_CODE_   │                     │
   │                          │   ANALYSIS             │                     │
   │                          │   reply_to: REPLY_xxx  │                     │
   │                          │                        │── Kafka Consume ──▶│
   │                          │                        │                     │── analizar AGENTS.md
   │                          │                        │                     │── LLM selecciona habilidad
   │                          │                        │                     │── ejecutar habilidad (stdin→stdout)
   │                          │                        │                     │── revisión LLM (opcional)
   │                          │                        │◀── Kafka Produce ──│
   │                          │                        │   topic: REPLY_xxx  │
   │                          │◀── Kafka Consume ──────│                     │
   │◀── resultado MCP ────────│                        │                     │
   │    o respuesta A2A       │                        │                     │
```

...existing code...

## Licencia

MIT

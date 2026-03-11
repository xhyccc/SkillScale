use std::sync::Arc;
use tokio::sync::oneshot;
use std::collections::HashMap;
use std::time::Duration;
use tracing::{info, error};
use rdkafka::producer::FutureRecord;
use rdkafka::util::Timeout;
use common::{SendTaskParams, Message as TransMessage, Part, Role as TransRole};
use crate::AppState;
use crate::skill_discovery;

use rmcp::{
    ServerHandler, ServiceExt,
    model::*,
    service::{RequestContext, RoleServer},
    transport::stdio,
};

#[derive(Clone)]
pub struct GatewayMcpServer {
    state: Arc<AppState>,
}

impl GatewayMcpServer {
    pub fn new(state: Arc<AppState>) -> Self {
        Self { state }
    }

    async fn invoke_kafka(
        &self,
        category: &str,
        skill_name: &str,
        input: &str,
    ) -> Result<String, String> {
        let topic = format!("TOPIC_{}", category.to_uppercase().replace("-", "_"));

        let task_id = uuid::Uuid::new_v4().to_string();
        
        // Add metadata required by skill servers
        // If skill_name is empty, the skillserver automatically uses AGENTS.md for matching (agent mode)
        let mut meta = HashMap::new();
        meta.insert("reply_to".to_string(), self.state.reply_topic.clone());
        meta.insert("request_id".to_string(), task_id.clone());
        meta.insert("skill".to_string(), skill_name.to_string());
        
        let params = SendTaskParams {
            id: task_id.clone(),
            session_id: Some("mcp_session".to_string()),
            message: TransMessage {
                role: TransRole::User,
                parts: vec![Part::Text { text: input.to_string() }],
            },
            metadata: Some(meta),
        };

        let payload_json = serde_json::to_string(&params).unwrap_or_default();
        if skill_name.is_empty() {
            info!("Invoking agent {} on topic {}", category, topic);
        } else {
            info!("Invoking skill {}/{} on topic {}", category, skill_name, topic);
        }
        
        let (tx, rx) = oneshot::channel();
        {
            let mut map = self.state.pending_requests.lock().unwrap();
            map.insert(task_id.clone(), tx);
        }

        let record = FutureRecord::to(&topic)
            .key(skill_name)
            .payload(&payload_json);

        match self.state.producer.send(record, Timeout::After(Duration::from_secs(5))).await {
            Ok(_) => {
                match tokio::time::timeout(self.state.gateway_timeout, rx).await {
                    Ok(Ok(response_val)) => {
                        let result_str = response_val.get("result")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string();
                        Ok(result_str)
                    },
                    Ok(Err(_)) => Err("Channel closed unexpectedly".to_string()),
                    Err(_) => {
                        {
                            let mut map = self.state.pending_requests.lock().unwrap();
                            map.remove(&task_id);
                        }
                        Err("Skill execution timeout".to_string())
                    }
                }
            },
            Err((e, _)) => {
               {
                   let mut map = self.state.pending_requests.lock().unwrap();
                   map.remove(&task_id);
               }
               Err(format!("Failed to produce message: {}", e))
            }
        }
    }
}

// remove async_trait for now, maybe it's not needed if we just implement it normally based on the RMCP lib 
// Note: sampling_stdio.rs doesn't use #[async_trait] and uses regular async fn (Rust 1.75+ maybe).
impl ServerHandler for GatewayMcpServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo::new(ServerCapabilities::builder().enable_tools().build())
            .with_instructions("Gateway MCP Server")
    }

    async fn list_tools(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
    ) -> Result<ListToolsResult, ErrorData> {
        let mut tools = vec![
            Tool::new(
                "ping",
                "Ping the server",
                Arc::new(
                    serde_json::from_value(serde_json::json!({
                        "type": "object",
                        "properties": {}
                    }))
                    .unwrap(),
                ),
            )
        ];

        // The gateway is run from the `skillscale-rs/gateway` directory or from the root.
        // Let's check both or use an absolute approach if possible.
        // Usually it's executed from the project root in our compose/scripts:
        let root1 = std::path::Path::new("skills");
        let root2 = std::path::Path::new("../../skills");
        
        // Discover agents
        let discovered_agents = if root1.exists() {
            skill_discovery::discover_agents(root1)
        } else {
            skill_discovery::discover_agents(root2)
        };
        
        for agent in discovered_agents {
            tools.push(Tool::new(
                format!("agent__{}", agent.category),
                agent.description.clone(),
                Arc::new(
                    serde_json::from_value(serde_json::json!({
                        "type": "object",
                        "properties": {
                            "input": { "type": "string" }
                        },
                        "required": ["input"]
                    }))
                    .unwrap(),
                ),
            ));
        }

        // Discover specific skills
        let discovered_skills = if root1.exists() {
            skill_discovery::discover_skills(root1)
        } else {
            skill_discovery::discover_skills(root2)
        };
        
        for skill in discovered_skills {
            tools.push(Tool::new(
                format!("{}__{}", skill.category, skill.name),
                skill.description.clone(),
                Arc::new(
                    serde_json::from_value(serde_json::json!({
                        "type": "object",
                        "properties": {
                            "input": { "type": "string" }
                        },
                        "required": ["input"]
                    }))
                    .unwrap(),
                ),
            ));
        }

        Ok(ListToolsResult {
            tools,
            meta: None,
            next_cursor: None,
        })
    }

    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        _context: RequestContext<RoleServer>,
    ) -> Result<CallToolResult, ErrorData> {
        let tool_name = request.name;
        let args = request.arguments.clone().unwrap_or_default();

        if tool_name == "ping" {
            return Ok(CallToolResult::success(vec![Content::text("Pong".to_string())]));
        }

        if let Some(cat) = tool_name.strip_prefix("agent__") {
             let input = args.get("input").and_then(|v| v.as_str()).unwrap_or("");
             match self.invoke_kafka(cat, "", input).await {
                 Ok(s) => return Ok(CallToolResult::success(vec![Content::text(s)])),
                 Err(e) => return Err(ErrorData::new(ErrorCode::INTERNAL_ERROR, e, None)),
             }
        }
        
        if let Some((cat, name)) = tool_name.split_once("__") {
             let input = args.get("input").and_then(|v| v.as_str()).unwrap_or("");
             match self.invoke_kafka(cat, name, input).await {
                 Ok(s) => return Ok(CallToolResult::success(vec![Content::text(s)])),
                 Err(e) => return Err(ErrorData::new(ErrorCode::INTERNAL_ERROR, e, None)),
             }
        }

        Err(ErrorData::new(ErrorCode::METHOD_NOT_FOUND, "Tool not found".to_string(), None))
    }
}

pub async fn run_stdio_server(state: Arc<AppState>) {
    let handler = GatewayMcpServer::new(state);
    
    // Create and serve using the method from sampling_stdio.rs
    match handler.serve(stdio()).await {
        Ok(service) => {
            if let Err(e) = service.waiting().await {
                error!("MCP Server Execution Error: {}", e);
            }
        },
        Err(e) => {
            error!("MCP Server Binding Error: {:?}", e);
        }
    }
}

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;

// --- A2A / JSON-RPC Wrappers ---

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JsonRpcRequest<T> {
    pub jsonrpc: String,
    pub id: Value,
    pub params: T,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JsonRpcResponse<T> {
    pub jsonrpc: String,
    pub id: Value,
    pub result: T,
}

// --- Domain Models ---

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SendTaskParams {
    pub id: String,
    pub session_id: Option<String>,
    pub message: Message,
    pub metadata: Option<HashMap<String, String>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub role: Role,
    pub parts: Vec<Part>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    User,
    Agent,
    System,
    Tool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")] 
pub enum Part {
    Text { text: String },
    // Extended A2A parts can be added here
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskResult {
    pub id: String,
    pub session_id: Option<String>,
    pub status: TaskStatus,
    pub history: Vec<Message>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskStatus {
    pub state: TaskState,
    pub timestamp: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum TaskState {
    Completed,
    Running,
    Failed,
    Pending,
}

// --- Topic / Internal Protocol ---

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillRequest {
    pub skill: String,
    pub data: Value,
    pub context: SkillContext,
    pub metadata: HashMap<String, String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillContext {
    pub session_id: String,
    pub protocol: String,
    #[serde(flatten)]
    pub extra: HashMap<String, Value>,
}

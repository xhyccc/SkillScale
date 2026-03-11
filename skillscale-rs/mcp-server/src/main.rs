use anyhow::Result;
use rdkafka::config::ClientConfig;
use rdkafka::consumer::{Consumer, StreamConsumer};
use rdkafka::message::Message;
use rdkafka::producer::{FutureProducer, FutureRecord};
use rdkafka::util::Timeout;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::sync::oneshot;
use tracing::{error, info, warn};

// --- MCP Protocol Structs (Simplified) ---

#[derive(Debug, Serialize, Deserialize)]
struct JsonRpcRequest {
    jsonrpc: String,
    method: String,
    #[serde(default)]
    params: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    id: Option<Value>,
}

#[derive(Debug, Serialize, Deserialize)]
struct JsonRpcResponse {
    jsonrpc: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<ValueError>,
    #[serde(skip_serializing_if = "Option::is_none")]
    id: Option<Value>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ValueError {
    code: i32,
    message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    data: Option<Value>,
}

// --- App State ---

struct AppState {
    producer: FutureProducer,
    reply_topic: String,
    pending_requests: Arc<Mutex<HashMap<String, oneshot::Sender<Value>>>>,
}

#[tokio::main]
async fn main() -> Result<()> {
    // Initialize logging to stderr so interferes less with stdout (mcp transport)
    tracing_subscriber::fmt()
        .with_writer(std::io::stderr)
        .with_ansi(false)
        .init();
    
    info!("Starting MCP Server (Rust)...");

    let broker_url = std::env::var("SKILLSCALE_BROKER_URL").unwrap_or_else(|_| "localhost:9092".to_string());
    let group_id = format!("mcp-group-{}", uuid::Uuid::new_v4());
    let reply_topic = format!("reply-client-{}", uuid::Uuid::new_v4()); 

    info!("Connecting to Kafka at {}, reply topic: {}", broker_url, reply_topic);

    // Create Producer
    let producer: FutureProducer = ClientConfig::new()
        .set("bootstrap.servers", &broker_url)
        .set("message.timeout.ms", "5000")
        .create()
        .expect("Producer creation error");

    // Create Consumer
    let consumer: StreamConsumer = ClientConfig::new()
        .set("group.id", &group_id)
        .set("bootstrap.servers", &broker_url)
        .set("enable.partition.eof", "false")
        .set("session.timeout.ms", "6000")
        .set("enable.auto.commit", "true")
        .set("auto.offset.reset", "earliest")
        .create()
        .expect("Consumer creation failed");

    consumer.subscribe(&[&reply_topic]).expect("Subscribe failed");

    let pending_requests: Arc<Mutex<HashMap<String, oneshot::Sender<Value>>>> = Arc::new(Mutex::new(HashMap::new()));
    let pending_clone = pending_requests.clone();

    // Spawn consumer loop
    let reply_topic_clone = reply_topic.clone();
    tokio::spawn(async move {
        info!("Listening for replies on {}", reply_topic_clone);
        loop {
            match consumer.recv().await {
                Ok(m) => {
                     let payload = match m.payload_view::<str>() {
                        None => "",
                        Some(Ok(s)) => s,
                        Some(Err(_)) => "",
                    };
                    if !payload.is_empty() {
                        // info!("Received Kafka reply: {}", payload);
                        if let Ok(json_val) = serde_json::from_str::<Value>(payload) {
                             // Check for request_id in metadata
                             if let Some(meta) = json_val.get("metadata") {
                                 if let Some(req_id) = meta.get("request_id").and_then(|v| v.as_str()) {
                                     let mut map = pending_clone.lock().unwrap();
                                     if let Some(tx) = map.remove(req_id) {
                                         let _ = tx.send(json_val);
                                     }
                                 }
                             }
                        }
                    }
                }
                Err(e) => warn!("Kafka error: {}", e),
            }
        }
    });

    let state = AppState {
        producer,
        reply_topic,
        pending_requests,
    };

    // Stdio Loop
    let stdin = tokio::io::stdin();
    let reader = BufReader::new(stdin);
    let mut lines = reader.lines();

    while let Ok(Some(line)) = lines.next_line().await {
        if line.trim().is_empty() { continue; }
        
        // Parse Request
        match serde_json::from_str::<JsonRpcRequest>(&line) {
            Ok(req) => {
                match handle_request(&state, req).await {
                    Some(resp) => {
                        let response_str = serde_json::to_string(&resp).unwrap();
                        println!("{}", response_str); 
                    }
                    None => {} // Notification handling (no response)
                }
            },
            Err(e) => {
                error!("Failed to parse JSON-RPC: {}", e);
            }
        }
    }

    Ok(())
}

async fn handle_request(state: &AppState, req: JsonRpcRequest) -> Option<JsonRpcResponse> {
    match req.method.as_str() {
        "initialize" => {
            Some(JsonRpcResponse {
                jsonrpc: "2.0".to_string(),
                id: req.id,
                result: Some(json!({
                    "protocolVersion": "2024-11-05", // MCP version
                    "capabilities": {
                        "tools": {},
                        "resources": {}
                    },
                    "serverInfo": {
                        "name": "skillscale-mcp-rust",
                        "version": "0.1.0"
                    }
                })),
                error: None,
            })
        },
        "notifications/initialized" => {
             // Just ack notification? Usually notifications don't get responses. 
             // But if `id` is present, it's a request.
             if req.id.is_some() {
                 Some(JsonRpcResponse {
                     jsonrpc: "2.0".to_string(),
                     id: req.id,
                     result: Some(json!(true)),
                     error: None
                 })
             } else {
                 None
             }
        },
        "tools/list" => {
            Some(JsonRpcResponse {
                jsonrpc: "2.0".to_string(),
                id: req.id,
                result: Some(json!({
                    "tools": [
                        {
                            "name": "invoke_skill",
                            "description": "Expose internal ZMQ skills to MCP clients.\n            This routes an MCP tool call transparently over ZMQ to the corresponding Skill Server.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "category": { "type": "string" },
                                    "skill_name": { "type": "string" },
                                    "payload": { "type": "object" }
                                },
                                "required": ["skill_name", "payload"]
                            }
                        }
                    ]
                })),
                error: None
            })
        },
        "resources/list" => {
             Some(JsonRpcResponse {
                jsonrpc: "2.0".to_string(),
                id: req.id,
                result: Some(json!({
                    "resources": [
                        {
                            "uri": "skillscale://context/session_123",
                            "name": "Example Shared Context",
                            "mimeType": "application/json"
                        }
                    ]
                })),
                error: None
            })
        },
        "resources/read" => {
             // Dispatch to Kafka topic TOPIC_CONTEXT_SYNC
             // Parse URI
             let uri = req.params["uri"].as_str().unwrap_or("");
             
             let topic = "TOPIC_CONTEXT_SYNC";
             let request_id = uuid::Uuid::new_v4().to_string();
             let payload = json!({
                 "action": "read",
                 "uri": uri,
                 "metadata": {
                     "reply_to": state.reply_topic,
                     "request_id": request_id
                 }
             });
             
             // Send to Kafka
             if let Ok(res) = send_and_wait(state, topic, &request_id, payload).await {
                  Some(JsonRpcResponse {
                    jsonrpc: "2.0".to_string(),
                    id: req.id,
                    result: Some(json!({
                        "contents": [
                            {
                                "uri": uri,
                                "mimeType": "application/json",
                                "text": res.to_string()
                            }
                        ]
                    })),
                    error: None
                 })
             } else {
                 Some(JsonRpcResponse {
                    jsonrpc: "2.0".to_string(),
                    id: req.id,
                    result: None,
                    error: Some(ValueError { code: -32603, message: "Timeout or Error".to_string(), data: None })
                 })
             }
        },
        "tools/call" => {
            let name = req.params["name"].as_str().unwrap_or("");
            let args = &req.params["arguments"];
            
            if name == "invoke_skill" {
                let skill_name = args["skill_name"].as_str().unwrap_or("");
                let category = args["category"].as_str().unwrap_or("CODE_ANALYSIS"); // default
                let inner_payload = &args["payload"];
                
                let topic = format!("TOPIC_{}", category.to_uppercase().replace("-", "_"));
                let request_id = uuid::Uuid::new_v4().to_string();
                
                // Construct skill payload
                let payload = json!({
                    "skill": skill_name,
                    "data": inner_payload,
                     "metadata": {
                        "reply_to": state.reply_topic,
                        "request_id": request_id,
                        "skill": skill_name
                     }
                });
                
                info!("Routing tool '{}' to Kafka '{}' (req: {})", skill_name, topic, request_id);
                
                match send_and_wait(state, &topic, &request_id, payload).await {
                    Ok(res) => {
                         // MCP expects content array
                         let mut text_res = res.to_string();
                         // Should we extract result from response? The skill returns full JSON {"result": ..., "status": ...}
                         if let Some(r) = res.get("result") {
                             if let Some(s) = r.as_str() {
                                 text_res = s.to_string();
                             } else {
                                 text_res = r.to_string();
                             }
                         }

                         Some(JsonRpcResponse {
                            jsonrpc: "2.0".to_string(),
                            id: req.id,
                            result: Some(json!({
                                "content": [
                                    {
                                        "type": "text",
                                        "text": text_res
                                    }
                                ]
                            })),
                            error: None
                         })
                    },
                    Err(_) => {
                        Some(JsonRpcResponse {
                            jsonrpc: "2.0".to_string(),
                            id: req.id,
                            result: None,
                            error: Some(ValueError { code: -32000, message: "Skill execution failed/timeout".to_string(), data: None })
                        })
                    }
                }

            } else {
                Some(JsonRpcResponse {
                    jsonrpc: "2.0".to_string(),
                    id: req.id,
                    result: None,
                    error: Some(ValueError { code: -32601, message: "Method not found".to_string(), data: None })
                })
            }
        },
        _ => {
            if req.id.is_some() {
                 Some(JsonRpcResponse {
                    jsonrpc: "2.0".to_string(),
                    id: req.id,
                    result: None,
                    error: Some(ValueError { code: -32601, message: "Method not found".to_string(), data: None })
                 })
            } else {
                 None
            }
        }
    }
}

async fn send_and_wait(state: &AppState, topic: &str, request_id: &str, payload: Value) -> Result<Value> {
    let payload_str = serde_json::to_string(&payload)?;
    
    // Setup oneshot
    let (tx, rx) = oneshot::channel();
    {
        let mut map = state.pending_requests.lock().unwrap();
        map.insert(request_id.to_string(), tx);
    }
    
    // Produce
    let record = FutureRecord::to(topic)
        .key(request_id)
        .payload(&payload_str);
        
    if let Err((e, _)) = state.producer.send(record, Timeout::After(Duration::from_secs(5))).await {
        {
            let mut map = state.pending_requests.lock().unwrap();
            map.remove(request_id);
        }
        anyhow::bail!("Kafka produce error: {}", e);
    }
    
    // Wait
    match tokio::time::timeout(Duration::from_secs(30), rx).await {
        Ok(Ok(val)) => Ok(val),
        Ok(Err(_)) => {
             // channel closed
             anyhow::bail!("Channel closed");
        },
        Err(_) => {
            {
                let mut map = state.pending_requests.lock().unwrap();
                map.remove(request_id);
            }
             anyhow::bail!("Timeout");
        }
    }
}

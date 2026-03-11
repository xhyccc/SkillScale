use axum::{
    extract::{Path, Json, State},
    routing::{get, post},
    Router,
};
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use std::collections::HashMap;
use std::time::Duration;
use tracing::{info, warn, error};
use serde_json::{Value, json};
use common::SendTaskParams;
use rdkafka::config::ClientConfig;
use rdkafka::producer::{FutureProducer, FutureRecord};
use rdkafka::consumer::{Consumer, StreamConsumer};
use rdkafka::message::Message as KafkaMessage;
use rdkafka::util::Timeout;
use tokio::sync::oneshot;
use clap::Parser;

mod mcp_server;
mod skill_discovery;

#[derive(Parser, Debug)]
#[command(author, version, about, long_about = None)]
struct Args {
    #[arg(long)]
    mcp: bool,
}

pub struct AppState {
    pub producer: FutureProducer,
    pub reply_topic: String,
    // Map request_id -> oneshot::Sender
    pub pending_requests: Arc<Mutex<HashMap<String, oneshot::Sender<Value>>>>,
    pub gateway_timeout: Duration,
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_max_level(tracing::Level::DEBUG)
        .with_writer(std::io::stderr)
        .init();
    
    let args = Args::parse();
    
    if args.mcp {
        // info! logs will go to stderr
        info!("Starting Gateway in MCP Server Mode...");
    } else {
        info!("Starting Gateway Service (A2A Bridge)...");
    }

    let broker_url = std::env::var("SKILLSCALE_BROKER_URL").unwrap_or_else(|_| "localhost:9092".to_string());
    
    // Parse gateway timeout from env, default to 300s
    let gateway_timeout_secs = std::env::var("SKILLSCALE_GATEWAY_TIMEOUT")
        .unwrap_or_else(|_| "300.0".to_string())
        .parse::<f64>()
        .unwrap_or(300.0);
    let gateway_timeout = Duration::from_secs_f64(gateway_timeout_secs);
    
    // Create a unique group ID to ensure we get a fresh consumer group
    let group_id = format!("gateway-group-{}", uuid::Uuid::new_v4());
    let reply_topic = format!("gateway-replies-{}", uuid::Uuid::new_v4());

    let producer: FutureProducer = ClientConfig::new()
        .set("bootstrap.servers", &broker_url)
        .set("message.timeout.ms", "5000")
        .create()
        .expect("Producer creation error");

    let pending_requests: Arc<Mutex<HashMap<String, oneshot::Sender<Value>>>> = Arc::new(Mutex::new(HashMap::new()));
    let pending_requests_clone = pending_requests.clone();

    // Start Reply Consumer in background task
    let reply_topic_clone = reply_topic.clone();
    let broker_url_clone = broker_url.clone();
    
    // Disable Kafka listener in MCP mode for now to isolate stdout issues
    if !args.mcp {
        tokio::spawn(async move {
            info!("Starting Reply Consumer on topic: {}", reply_topic_clone);
            
            let consumer: StreamConsumer = ClientConfig::new()
                .set("group.id", &group_id)
                .set("bootstrap.servers", &broker_url_clone)
                .set("enable.partition.eof", "false")
                .set("session.timeout.ms", "6000")
                .set("enable.auto.commit", "true")
                .set("auto.offset.reset", "earliest")
                .create()
                .expect("Consumer creation failed");

            if let Err(e) = consumer.subscribe(&[&reply_topic_clone]) {
                error!("Failed to subscribe to reply topic: {}", e);
                return;
            }

            loop {
                match consumer.recv().await {
                    Err(e) => warn!("Kafka error: {}", e),
                    Ok(m) => {
                        let payload = match m.payload_view::<str>() {
                            None => "",
                            Some(Ok(s)) => s,
                            Some(Err(_)) => "",
                        };
                        
                        if !payload.is_empty() {
                             info!("Received reply: {}", payload);
                             if let Ok(json_val) = serde_json::from_str::<Value>(payload) {
                                 if let Some(meta) = json_val.get("metadata") {
                                     if let Some(req_id) = meta.get("request_id").and_then(|v| v.as_str()) {
                                         let mut map = pending_requests_clone.lock().unwrap();
                                         info!("Looking for req_id: {}, keys in map: {:?}", req_id, map.keys().collect::<Vec<_>>());
                                         if let Some(tx) = map.remove(req_id) {
                                             let _ = tx.send(json_val);
                                         }
                                     }
                                 }
                             }
                        }
                    }
                }
            }
        });
    }

    let state = Arc::new(AppState {
        producer,
        reply_topic,
        pending_requests,
        gateway_timeout,
    });

    if args.mcp {
        mcp_server::run_stdio_server(state).await;
        return;
    }

    let app = Router::new()
        .route("/health", get(|| async { "OK" }))
        .route("/v1/agents/:agent_id/converse", post(handle_converse))
        .with_state(state);

    let addr = SocketAddr::from(([0, 0, 0, 0], 8085));
    info!("Gateway listening on {}", addr);
    
    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

async fn handle_converse(
    Path(agent_id): Path<String>,
    State(state): State<Arc<AppState>>,
    Json(mut params): Json<SendTaskParams>,
) -> Json<Value> {
    info!("Received converse request for agent: {} (ID: {})", agent_id, params.id);
    
    let topic = format!("TOPIC_{}", agent_id.replace("-", "_").to_uppercase());
    info!("Routing to topic: {}", topic);

    // Inject reply_to and request_id into metadata
    if params.metadata.is_none() {
        params.metadata = Some(HashMap::new());
    }
    if let Some(meta) = params.metadata.as_mut() {
        meta.insert("reply_to".to_string(), state.reply_topic.clone());
        meta.insert("request_id".to_string(), params.id.clone());
        meta.insert("skill".to_string(), agent_id.clone()); // also inject skill name
    }

    let payload_json = serde_json::to_string(&params).unwrap_or_default();
    
    // Create channel to wait for response
    let (tx, rx) = oneshot::channel();
    {
        let mut map = state.pending_requests.lock().unwrap();
        map.insert(params.id.clone(), tx);
    }

    let record = FutureRecord::to(&topic)
        .key(&agent_id)
        .payload(&payload_json);

    match state.producer.send(record, Timeout::After(Duration::from_secs(5))).await {
        Ok(_) => {
            // Wait for response with timeout from config
            match tokio::time::timeout(state.gateway_timeout, rx).await {
                Ok(Ok(response_val)) => {
                    // Start forming the A2A response
                    // Extract result string from the response
                    let result_str = response_val.get("result").and_then(|v| v.as_str()).unwrap_or("");
                    
                    Json(json!({
                        "jsonrpc": "2.0",
                        "id": params.id,
                        "result": {
                            "id": params.id,
                            "status": {
                                "state": "completed",
                                "timestamp": chrono::Utc::now().to_rfc3339()
                            },
                            // Add history message with the result
                            "history": [
                                {
                                    "role": "assistant",
                                    "parts": [{ "type": "text", "text": result_str }]
                                }
                            ]
                        }
                    }))
                },
                Ok(Err(_)) => {
                    Json(json!({
                        "jsonrpc": "2.0",
                        "id": params.id,
                        "error": { "code": -32603, "message": "Channel closed unexpectedly" }
                    }))
                },
                Err(_) => {
                    // Timeout
                    {
                        let mut map = state.pending_requests.lock().unwrap();
                        map.remove(&params.id);
                    }
                     Json(json!({
                        "jsonrpc": "2.0",
                        "id": params.id,
                        "error": { "code": -32000, "message": "Skill execution timeout" }
                    }))
                }
            }
        },
        Err((e, _)) => {
            error!("Failed to produce message: {}", e);
            {
                 let mut map = state.pending_requests.lock().unwrap();
                 map.remove(&params.id);
            }
            Json(json!({
                "jsonrpc": "2.0",
                "id": params.id,
                "error": { "code": -32603, "message": "Internal error producing message" }
            }))
        }
    }
}

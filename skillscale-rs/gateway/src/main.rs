use axum::{
    extract::{Path, Json, State},
    routing::{get, post},
    Router,
};
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;
use tracing::{info, error};
use serde_json::{Value, json};
use common::{SendTaskParams, TaskResult, TaskStatus, TaskState, Message, Part, Role};
use rdkafka::config::ClientConfig;
use rdkafka::producer::{FutureProducer, FutureRecord};
use rdkafka::util::Timeout;

struct AppState {
    producer: FutureProducer,
    topic: String,
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt::init();

    info!("Starting Gateway Service...");

    let producer: FutureProducer = ClientConfig::new()
        // Use environment variable for bootstrap servers with default
        .set("bootstrap.servers", std::env::var("SKILLSCALE_BROKER_URL").unwrap_or_else(|_| "localhost:9092".to_string()))
        .set("message.timeout.ms", "5000")
        .create()
        .expect("Producer creation error");

    let state = Arc::new(AppState {
        producer,
        topic: "skill.request".to_string(),
    });

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
    Json(params): Json<SendTaskParams>,
) -> Json<Value> {
    info!("Received converse request for agent: {} (ID: {})", agent_id, params.id);
    
    // Determine topic from agent_id: e.g. "code-analysis" -> "TOPIC_CODE_ANALYSIS"
    // This assumes the conventions set by build.sh
    let topic = format!("TOPIC_{}", agent_id.replace("-", "_").to_uppercase());
    info!("Routing to topic: {}", topic);

    // Serialize the incoming params to forward as JSON
    let payload_json = serde_json::to_string(&params).unwrap_or_default();
    
    // Produce to Kafka using the dynamic topic
    let record = FutureRecord::to(&topic)
        .key(&agent_id) // Use agent_id as key for partitioning
        .payload(&payload_json);

    // Note: state.topic is ignored here in favor of dynamic routing
    match state.producer.send(record, Timeout::After(Duration::from_secs(5))).await {
        Ok((partition, offset)) => {
            info!("Produced message to partition {} at offset {}", partition, offset);
        },
        Err((e, _)) => {
            error!("Failed to produce message: {}", e);
            return Json(json!({
                "jsonrpc": "2.0",
                "id": params.id,
                "error": { "code": -32603, "message": "Internal error producing message" }
            }));
        }
    }
    
    // For now, return a generic "Pending" or "Completed" response since we aren't waiting for the reply yet
    Json(json!({
        "jsonrpc": "2.0",
        "id": params.id,
        "result": {
            "id": params.id,
            "status": {
                "state": "running",
                "timestamp": chrono::Utc::now().to_rfc3339()
            },
            "history": []
        }
    }))
}

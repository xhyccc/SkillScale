use anyhow::{Context, Result};
use std::path::{Path, PathBuf};
use tracing::{info, warn, error};
use std::process::Stdio;
use tokio::process::Command;
use tokio::io::AsyncWriteExt; // Import AsyncWriteExt for write_all
use rdkafka::config::ClientConfig;
use rdkafka::consumer::{Consumer, StreamConsumer};
use rdkafka::message::Message;
use common::{SendTaskParams, Part};

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt::init();
    info!("Starting Skill Server (Rust)...");

    let exec_path = find_opencode_exec()?;
    info!("Found executor: {:?}", exec_path);

    let consumer: StreamConsumer = ClientConfig::new()
        .set("group.id", std::env::var("SKILLSCALE_GROUP_ID").unwrap_or_else(|_| "skill-server-group".to_string()))
        .set("bootstrap.servers", std::env::var("SKILLSCALE_BROKER_URL").unwrap_or_else(|_| "localhost:9092".to_string()))
        .set("enable.partition.eof", "false")
        .set("session.timeout.ms", "6000")
        .set("enable.auto.commit", "true")
        .create()
        .context("Consumer creation failed")?;

    let topic = std::env::var("SKILLSCALE_TOPIC").unwrap_or_else(|_| "skill.request".to_string());
    consumer.subscribe(&[&topic])
        .context("Can't subscribe to topic")?;

    info!("Subscribed to '{}'. Waiting for messages...", topic);

    loop {
        match consumer.recv().await {
            Err(e) => warn!("Kafka error: {}", e),
            Ok(m) => {
                let payload = match m.payload_view::<str>() {
                    None => "",
                    Some(Ok(s)) => s,
                    Some(Err(e)) => {
                        warn!("Error while deserializing message payload: {:?}", e);
                        ""
                    }
                };
                
                info!("Received message: {}", payload);
                if !payload.is_empty() {
                    // Try to parse as SendTaskParams to extract skill name and input text
                    let (skill_name, skill_input) = match serde_json::from_str::<SendTaskParams>(payload) {
                        Ok(params) => {
                            let s = params.metadata.as_ref()
                                .and_then(|m| m.get("skill").cloned())
                                .unwrap_or_else(|| String::new());
                            
                            let text = params.message.parts.iter()
                                .filter_map(|p| match p {
                                    Part::Text { text } => Some(text.as_str()),
                                    _ => None,
                                })
                                .collect::<Vec<&str>>()
                                .join("\n");
                            
                            (s, text)
                        }
                        Err(_) => {
                            // Fallback: try parsing as generic JSON {"skill": ..., "input": ...}
                            match serde_json::from_str::<serde_json::Value>(payload) {
                                Ok(v) => {
                                    let s = v["skill"].as_str().unwrap_or("").to_string();
                                    let i = v["input"].as_str().unwrap_or(payload).to_string();
                                    (s, i)
                                }
                                Err(_) => (String::new(), payload.to_string()),
                            }
                        }
                    };

                    info!("Executing skill: '{}' with input len: {}", skill_name, skill_input.len());
                    
                    match execute_skill(&exec_path, &skill_name, &skill_input).await {
                        Ok(output) => {
                            info!("Skill execution successful. Output:\n{}", output);
                        },
                        Err(e) => {
                            error!("Execution failed: {:?}", e);
                        }
                    }
                }
            }
        }
    }
}

fn find_opencode_exec() -> Result<PathBuf> {
    let variants = vec![
        PathBuf::from("./scripts/opencode-exec"),
        PathBuf::from("../scripts/opencode-exec"),
        PathBuf::from("../../scripts/opencode-exec"),
    ];

    for p in variants {
        if p.exists() {
            return Ok(p.canonicalize()?);
        }
    }
    
    // Fallback: check project root via ENV
    if let Ok(root) = std::env::var("SKILLSCALE_ROOT") {
        let p = Path::new(&root).join("scripts/opencode-exec");
        if p.exists() {
            return Ok(p);
        }
    }

    anyhow::bail!("Cannot find scripts/opencode-exec. Please run from project root or set SKILLSCALE_ROOT.");
}

async fn execute_skill(exec_path: &Path, skill_name: &str, intent: &str) -> Result<String> {
    
    let mut cmd = Command::new(exec_path);
    if !skill_name.is_empty() {
        cmd.arg(skill_name);
    }
    
    let mut child = cmd
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .context("Failed to spawn executor process")?;

    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(intent.as_bytes()).await.context("Failed to write to stdin")?;
    }

    let output = child.wait_with_output().await.context("Failed to wait for output")?;
        
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        anyhow::bail!("Skill execution failed: {}", stderr);
    }
    
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

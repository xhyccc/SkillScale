#pragma once

#include "skill_loader.h"
#include <string>
#include <atomic>

// Forward declaration
struct Config;

/**
 * Service to run the Kafka/Redpanda consumer loop.
 * This runs in the main thread (blocking), consuming messages and executing skills.
 */
void run_kafka_service(const Config& cfg, SkillLoader& loader, std::atomic<bool>& running);

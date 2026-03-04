#include "kafka_service.h"
#include "server_config.h"
#include "skill_executor.h"

#include <iostream>
#include <thread>
#include <chrono>
#include <librdkafka/rdkafkacpp.h>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

class KafkaDeliveryReportCb : public RdKafka::DeliveryReportCb {
public:
    void dr_cb(RdKafka::Message &message) override {
        if (message.err()) {
            std::cerr << "[kafka] Delivery failed: " << message.errstr() << std::endl;
        }
    }
};

void run_kafka_service(const Config& cfg, SkillLoader& loader, std::atomic<bool>& running) {
    std::string errstr;

    // ── Configure Producer (for replies) ──
    RdKafka::Conf *p_conf = RdKafka::Conf::create(RdKafka::Conf::CONF_GLOBAL);
    p_conf->set("bootstrap.servers", cfg.brokers, errstr);
    
    KafkaDeliveryReportCb dr_cb;
    p_conf->set("dr_cb", &dr_cb, errstr);

    RdKafka::Producer *producer = RdKafka::Producer::create(p_conf, errstr);
    if (!producer) {
        std::cerr << "[kafka] Failed to create producer: " << errstr << std::endl;
        delete p_conf;
        return;
    }
    delete p_conf; // Producer took ownership of conf? No, usually distinct. The create method copies.

    // ── Configure Consumer (for tasks) ──
    RdKafka::Conf *c_conf = RdKafka::Conf::create(RdKafka::Conf::CONF_GLOBAL);
    c_conf->set("bootstrap.servers", cfg.brokers, errstr);
    c_conf->set("group.id", cfg.group_id.empty() ? "skillscale-group-" + std::to_string(std::rand()) : cfg.group_id, errstr);
    c_conf->set("auto.offset.reset", "latest", errstr);
    c_conf->set("enable.auto.commit", "true", errstr);

    RdKafka::KafkaConsumer *consumer = RdKafka::KafkaConsumer::create(c_conf, errstr);
    if (!consumer) {
        std::cerr << "[kafka] Failed to create consumer: " << errstr << std::endl;
        delete c_conf;
        delete producer;
        return;
    }
    delete c_conf;

    // ── Subscribe ──
    std::vector<std::string> topics;
    topics.push_back(cfg.topic);
    RdKafka::ErrorCode err = consumer->subscribe(topics);
    if (err) {
        std::cerr << "[kafka] Failed to subscribe to " << cfg.topic << ": " << RdKafka::err2str(err) << std::endl;
        delete consumer;
        delete producer;
        return;
    }

    std::cout << "[kafka] Listening on " << cfg.topic << " (brokers: " << cfg.brokers << ")\n";

    SkillExecutor executor(cfg.timeout, cfg.python);

    while (running.load()) {
        RdKafka::Message *msg = consumer->consume(200);
        if (!msg) continue;

        if (msg->err() == RdKafka::ERR_NO_ERROR) {
            std::string payload(static_cast<const char *>(msg->payload()), msg->len());
            //std::cout << "[kafka] Received: " << payload.substr(0, 50) << "...\n";

            try {
                auto j = json::parse(payload);
                
                // Extract fields
                std::string intent_str;
                std::string skill_name;
                std::string request_id;
                std::string reply_to;

                if (j.contains("intent")) {
                    // Gateway format often wraps: {"intent": "...", "metadata": ...}
                    // But currently gateway sends direct JSON string as intent?
                    // Let's check python implementation.
                    // Python sends: {"intent": json_string_of_payload, "metadata": {...}}
                    std::string inner_json_str = j["intent"].get<std::string>();
                    
                    // The inner string is the actual payload: {"skill": "...", "data": ...}
                    auto inner_j = json::parse(inner_json_str);
                    
                    if (inner_j.contains("skill")) skill_name = inner_j["skill"];
                    if (inner_j.contains("data") && inner_j["data"].contains("input")) {
                        intent_str = inner_j["data"]["input"];
                    }
                }

                if (j.contains("metadata")) {
                    auto meta = j["metadata"];
                    if (meta.contains("reply_to")) reply_to = meta["reply_to"];
                    if (meta.contains("request_id")) request_id = meta["request_id"];
                }

                std::cout << "[kafka] Task " << request_id << " for skill '" << skill_name << "'\n";

                // Find skill definition
                SkillDefinition skill_def;
                bool found = false;
                for (const auto& [name, s] : loader.skills()) {
                    if (name == skill_name) {
                        skill_def = s;
                        found = true;
                        break;
                    }
                }

                ExecutionResult result;
                if (found) {
                    result = executor.execute(skill_def, intent_str);
                } else {
                    // Fallback or direct execution logic if needed
                    // For now, if skill not found, maybe try to match or error
                    std::cerr << "[kafka] Skill '" << skill_name << "' not found locally.\n";
                    result.success = false;
                    result.stderr_output = "Skill not found: " + skill_name;
                }

                if (reply_to.empty()) {
                    delete msg;
                    continue;
                }

                // Construct response
                json resp;
                if (result.success) {
                    resp["status"] = "success";
                    resp["result"] = result.stdout_output;
                } else {
                    resp["status"] = "error";
                    resp["result"] = result.stderr_output;
                }
                resp["metadata"] = {{"request_id", request_id}};

                std::string resp_str = resp.dump();

                // Produce reply
                producer->produce(
                    reply_to,
                    RdKafka::Topic::PARTITION_UA,
                    RdKafka::Producer::RK_MSG_COPY,
                    const_cast<char *>(resp_str.c_str()), resp_str.size(),
                    NULL, 0,
                    0, NULL // timestamp=0, opaque=NULL
                );
                producer->poll(0); // Trigger callbacks

            } catch (const std::exception& e) {
                std::cerr << "[kafka] Error processing message: " << e.what() << "\n";
            }

        } else if (msg->err() != RdKafka::ERR__TIMED_OUT) {
             std::cerr << "[kafka] Consume error: " << msg->errstr() << "\n";
        }

        delete msg;
    }
    
    std::cout << "[kafka] Shutting down...\n";
    producer->flush(2000);
    consumer->close();
    delete consumer;
    delete producer;
    RdKafka::wait_destroyed(5000);
}

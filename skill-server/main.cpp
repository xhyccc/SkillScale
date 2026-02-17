/**
 * SkillScale — C++ Skill Server
 *
 * Subscribes to a specific ZeroMQ topic, receives intent requests,
 * executes the matching skill via subprocess (POSIX fork/exec),
 * and publishes the result back on the reply_to topic.
 *
 * Usage:
 *   skillscale_skill_server --topic TOPIC_DATA_PROCESSING \
 *                           --skills-dir ./skills/data-processing \
 *                           --proxy-xpub tcp://proxy:5555 \
 *                           --proxy-xsub tcp://proxy:5444
 */

#include <zmq.hpp>
#include <nlohmann/json.hpp>

#include "skill_loader.h"
#include "skill_executor.h"
#include "message_handler.h"

#include <atomic>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

using json = nlohmann::json;

static std::atomic<bool> g_running{true};

static void signal_handler(int) { g_running.store(false); }

// ──────────────────────────────────────────────────────────
//  CLI argument parsing (simple key-value)
// ──────────────────────────────────────────────────────────
struct Config {
    std::string topic       = "TOPIC_DEFAULT";
    std::string description = "";            // human-readable server description
    std::string skills_dir  = "./skills";
    std::string proxy_xpub  = "tcp://127.0.0.1:5555";
    std::string proxy_xsub  = "tcp://127.0.0.1:5444";
    std::string matcher     = "llm";         // "llm" or "keyword"
    std::string prompt_file = "";            // optional custom prompt template
    std::string python      = "python3";     // Python executable for LLM subprocess
    int         hwm         = 10000;
    int         heartbeat   = 5000;   // ms
    int         timeout     = 30000;  // skill execution timeout ms
    int         workers     = 2;      // concurrent skill execution threads
};

static Config parse_args(int argc, char* argv[]) {
    Config cfg;

    // Override from environment first
    if (auto v = std::getenv("SKILLSCALE_TOPIC"))      cfg.topic = v;
    if (auto v = std::getenv("SKILLSCALE_DESCRIPTION"))cfg.description = v;
    if (auto v = std::getenv("SKILLSCALE_SKILLS_DIR")) cfg.skills_dir = v;
    if (auto v = std::getenv("SKILLSCALE_PROXY_XPUB")) cfg.proxy_xpub = v;
    if (auto v = std::getenv("SKILLSCALE_PROXY_XSUB")) cfg.proxy_xsub = v;
    if (auto v = std::getenv("SKILLSCALE_HWM"))        cfg.hwm = std::atoi(v);
    if (auto v = std::getenv("SKILLSCALE_TIMEOUT"))    cfg.timeout = std::atoi(v);
    if (auto v = std::getenv("SKILLSCALE_WORKERS"))    cfg.workers = std::atoi(v);
    if (auto v = std::getenv("SKILLSCALE_MATCHER"))    cfg.matcher = v;
    if (auto v = std::getenv("SKILLSCALE_PROMPT_FILE"))cfg.prompt_file = v;
    if (auto v = std::getenv("SKILLSCALE_PYTHON"))     cfg.python = v;

    // CLI overrides
    for (int i = 1; i < argc - 1; i += 2) {
        std::string key = argv[i];
        std::string val = argv[i + 1];
        if (key == "--topic")      cfg.topic = val;
        else if (key == "--description")  cfg.description = val;
        else if (key == "--skills-dir")  cfg.skills_dir = val;
        else if (key == "--proxy-xpub")  cfg.proxy_xpub = val;
        else if (key == "--proxy-xsub")  cfg.proxy_xsub = val;
        else if (key == "--hwm")         cfg.hwm = std::stoi(val);
        else if (key == "--timeout")     cfg.timeout = std::stoi(val);
        else if (key == "--workers")     cfg.workers = std::stoi(val);
        else if (key == "--matcher")     cfg.matcher = val;
        else if (key == "--prompt-file") cfg.prompt_file = val;
        else if (key == "--python")      cfg.python = val;
    }

    return cfg;
}

// ──────────────────────────────────────────────────────────
//  Worker thread — picks requests from an inproc queue,
//  executes skills, publishes results back to the proxy.
// ──────────────────────────────────────────────────────────
static void worker_thread(zmq::context_t& ctx,
                          const Config& cfg,
                          SkillLoader& loader) {
    // Each worker has its own PUB socket to the proxy XSUB
    zmq::socket_t pub(ctx, zmq::socket_type::pub);
    pub.set(zmq::sockopt::sndhwm, cfg.hwm);
    pub.set(zmq::sockopt::linger, 1000);
    pub.connect(cfg.proxy_xsub);

    // Inproc PULL socket to receive work from the main thread
    zmq::socket_t pull(ctx, zmq::socket_type::pull);
    pull.connect("inproc://workers");

    SkillExecutor executor(cfg.timeout, cfg.python);

    while (g_running.load()) {
        zmq::pollitem_t item = {static_cast<void*>(pull), 0, ZMQ_POLLIN, 0};
        zmq::poll(&item, 1, std::chrono::milliseconds(500));

        if (!(item.revents & ZMQ_POLLIN)) continue;

        // Receive 2 frames: topic + payload
        zmq::message_t topic_msg, payload_msg;
        auto rc1 = pull.recv(topic_msg, zmq::recv_flags::dontwait);
        if (!rc1) continue;
        auto rc2 = pull.recv(payload_msg, zmq::recv_flags::dontwait);
        if (!rc2) continue;

        std::string topic_str(static_cast<char*>(topic_msg.data()), topic_msg.size());
        std::string payload_str(static_cast<char*>(payload_msg.data()), payload_msg.size());

        auto req = MessageHandler::parse_request(topic_str, payload_str);

        if (!req.valid) {
            std::cerr << "[worker] Invalid request: " << req.parse_error << "\n";
            continue;
        }

        std::cout << "[worker] Processing request " << req.request_id
                  << " intent: " << req.intent.substr(0, 80) << "\n";

        // ── Find matching skill ──
        // Supports two intent modes:
        //   Mode 1 (explicit):  {"skill": "csv-analyzer", "data": "..."}
        //   Mode 2 (task-based): plain text or {"task": "analyze this csv data..."}
        const SkillDefinition* skill = nullptr;
        std::string exec_input = req.intent; // data to pass to the script

        bool explicit_skill = false;
        // Attempt to parse intent as JSON
        try {
            json intent_json = json::parse(req.intent);

            // Mode 1: explicit skill name
            if (intent_json.contains("skill")) {
                skill = loader.find(intent_json["skill"].get<std::string>());
                explicit_skill = true;
            }

            // Extract the "data" / "task" field for the script
            if (intent_json.contains("data")) {
                exec_input = intent_json["data"].get<std::string>();
            } else if (intent_json.contains("task")) {
                exec_input = intent_json["task"].get<std::string>();
            }

            // Mode 2: task description — match against skill descriptions
            if (!explicit_skill && intent_json.contains("task")) {
                std::string task = intent_json["task"].get<std::string>();
                std::cout << "[worker] Mode 2: matching task (" << loader.matcher_mode() << ")\n";
                skill = loader.match_task(task);
            }
        } catch (...) {
            // Intent is plain text — Mode 2: match by description
            std::cout << "[worker] Mode 2: plain text intent, matching (" << loader.matcher_mode() << ")\n";
            skill = loader.match_task(req.intent);
        }

        // Fallback: use first skill if single-skill server
        if (!skill && !loader.skills().empty() && loader.skills().size() == 1) {
            skill = &loader.skills().begin()->second;
            std::cout << "[worker] Fallback: single skill server, using " << skill->name << "\n";
        }

        OutgoingResponse resp;
        if (!skill) {
            resp = MessageHandler::make_error(
                req.request_id, req.reply_to,
                "No matching skill found for topic: " + req.topic);
        } else {
            // ── Progressive disclosure: load full SKILL.md on demand ──
            auto& mutable_skill = loader.skills()[skill->name];
            if (!mutable_skill.details_loaded) {
                std::cout << "[worker] Progressive disclosure: loading details for '"
                          << mutable_skill.name << "'\n";
                loader.load_skill_details(mutable_skill);
            }

            auto exec_result = executor.execute(mutable_skill, exec_input);

            if (exec_result.success) {
                resp = MessageHandler::make_success(
                    req.request_id, req.reply_to,
                    exec_result.stdout_output);
            } else {
                resp = MessageHandler::make_error(
                    req.request_id, req.reply_to,
                    "Skill execution failed (exit=" +
                    std::to_string(exec_result.exit_code) + "): " +
                    exec_result.stderr_output);
            }
        }

        // Publish response on the reply_to topic
        std::string resp_payload = MessageHandler::serialize_response(resp);

        pub.send(zmq::buffer(resp.reply_to), zmq::send_flags::sndmore);
        pub.send(zmq::buffer(resp_payload), zmq::send_flags::none);

        std::cout << "[worker] Published response on topic: "
                  << resp.reply_to << "\n";
    }
}

// ──────────────────────────────────────────────────────────
//  Main
// ──────────────────────────────────────────────────────────
int main(int argc, char* argv[]) {
    // Force line-buffered stdout so logs appear immediately when redirected
    setvbuf(stdout, nullptr, _IOLBF, 0);
    setvbuf(stderr, nullptr, _IOLBF, 0);

    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    Config cfg = parse_args(argc, argv);

    std::cout << "[server] SkillScale Skill Server starting\n"
              << "[server]   Topic      : " << cfg.topic << "\n"
              << "[server]   Description: " << (cfg.description.empty() ? "(none)" : cfg.description) << "\n"
              << "[server]   Skills dir : " << cfg.skills_dir << "\n"
              << "[server]   Proxy XPUB : " << cfg.proxy_xpub << "\n"
              << "[server]   Proxy XSUB : " << cfg.proxy_xsub << "\n"
              << "[server]   HWM        : " << cfg.hwm << "\n"
              << "[server]   Workers    : " << cfg.workers << "\n"
              << "[server]   Matcher    : " << cfg.matcher << "\n"
              << "[server]   Prompt file: " << (cfg.prompt_file.empty() ? "(default)" : cfg.prompt_file) << "\n"
              << "[server]   Python     : " << cfg.python << "\n";

    // ── Load skills ──
    SkillLoader loader(cfg.skills_dir);
    loader.set_matcher(cfg.matcher);
    if (!cfg.prompt_file.empty()) {
        loader.set_prompt_file(cfg.prompt_file);
    }
    loader.set_python(cfg.python);
    int loaded = loader.load_all();
    if (loaded == 0) {
        std::cerr << "[server] WARNING: No skills loaded from "
                  << cfg.skills_dir << "\n";
    }

    // ── Broadcast skill metadata (for progressive disclosure) ──
    json metadata;
    metadata["topic"] = cfg.topic;
    metadata["description"] = cfg.description;
    metadata["intent_modes"] = json::array({"explicit", "task-based"});
    metadata["matcher"] = cfg.matcher;
    metadata["skills"] = json::array();
    for (auto& [name, skill] : loader.skills()) {
        json s;
        s["name"] = skill.name;
        s["description"] = skill.description;
        metadata["skills"].push_back(s);
    }
    std::cout << "[server] Skill metadata: " << metadata.dump(2) << "\n";

    // ── ZeroMQ setup ──
    zmq::context_t ctx(2);

    // Subscriber socket — receives intent broadcasts from the proxy
    zmq::socket_t sub(ctx, zmq::socket_type::sub);
    sub.set(zmq::sockopt::rcvhwm, cfg.hwm);
    sub.set(zmq::sockopt::tcp_keepalive, 1);
    sub.set(zmq::sockopt::tcp_keepalive_idle, 60);
    sub.set(zmq::sockopt::heartbeat_ivl, cfg.heartbeat);
    sub.set(zmq::sockopt::heartbeat_ttl, cfg.heartbeat * 3);
    sub.set(zmq::sockopt::heartbeat_timeout, cfg.heartbeat * 3);
    sub.set(zmq::sockopt::reconnect_ivl, 100);
    sub.set(zmq::sockopt::reconnect_ivl_max, 5000);
    sub.connect(cfg.proxy_xpub);

    // Subscribe to our specific topic
    sub.set(zmq::sockopt::subscribe, cfg.topic);
    std::cout << "[server] Subscribed to: " << cfg.topic << "\n";

    // Inproc PUSH socket — distributes work to worker threads
    zmq::socket_t push(ctx, zmq::socket_type::push);
    push.bind("inproc://workers");

    // ── Synchronization delay to avoid late-joiner syndrome ──
    std::cout << "[server] Waiting for subscription propagation...\n";
    std::this_thread::sleep_for(std::chrono::milliseconds(500));

    // ── Spawn worker threads ──
    std::vector<std::thread> workers;
    for (int i = 0; i < cfg.workers; ++i) {
        workers.emplace_back(worker_thread, std::ref(ctx),
                             std::cref(cfg), std::ref(loader));
    }

    std::cout << "[server] Ready. Listening for intents on " << cfg.topic << "\n";

    // ── Main event loop — receive from SUB, dispatch to workers ──
    while (g_running.load()) {
        zmq::pollitem_t item = {static_cast<void*>(sub), 0, ZMQ_POLLIN, 0};
        zmq::poll(&item, 1, std::chrono::milliseconds(250));

        if (!(item.revents & ZMQ_POLLIN)) continue;

        zmq::message_t topic_msg, payload_msg;
        auto rc1 = sub.recv(topic_msg, zmq::recv_flags::dontwait);
        if (!rc1) continue;
        auto rc2 = sub.recv(payload_msg, zmq::recv_flags::dontwait);
        if (!rc2) continue;

        // Forward to workers via inproc PUSH/PULL pipeline
        push.send(topic_msg, zmq::send_flags::sndmore);
        push.send(payload_msg, zmq::send_flags::none);
    }

    std::cout << "[server] Shutting down...\n";
    for (auto& w : workers) w.join();

    return 0;
}

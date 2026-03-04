#pragma once
#include <string>

struct Config {
    std::string backend     = "zeromq";      // "zeromq" or "redpanda"
    std::string topic       = "TOPIC_DEFAULT";
    std::string description = "";            // human-readable server description
    std::string skills_dir  = "./skills";
    
    // ZeroMQ specific
    std::string proxy_xpub  = "tcp://127.0.0.1:5555";
    std::string proxy_xsub  = "tcp://127.0.0.1:5444";
    
    // Kafka/Redpanda specific
    std::string brokers     = "localhost:9092";
    std::string group_id    = "";            // if empty, generated randomly

    std::string matcher     = "llm";         // "llm" or "keyword"
    std::string prompt_file = "";            // optional custom prompt template
    std::string python      = "python3";     // Python executable for LLM subprocess
    int         hwm         = 10000;
    int         heartbeat   = 5000;          // ms
    int         timeout     = 180000;        // skill execution timeout ms (via SKILLSCALE_TIMEOUT)
    int         workers     = 2;             // concurrent skill execution threads
};

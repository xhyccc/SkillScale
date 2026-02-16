#pragma once

#include <nlohmann/json.hpp>
#include <string>

using json = nlohmann::json;

/**
 * Handles ZeroMQ message parsing/serialization.
 *
 * Request envelope:
 *   Frame 0: Topic string (e.g. "TOPIC_DATA_PROCESSING")
 *   Frame 1: JSON payload { request_id, reply_to, intent, timestamp }
 *
 * Response envelope:
 *   Frame 0: reply_to topic string
 *   Frame 1: JSON payload { request_id, status, content, error }
 */
struct IncomingRequest {
    std::string topic;
    std::string request_id;
    std::string reply_to;
    std::string intent;
    double timestamp = 0.0;
    bool valid = false;
    std::string parse_error;
};

struct OutgoingResponse {
    std::string reply_to;    // topic frame
    std::string request_id;
    std::string status;      // "success" | "error" | "timeout"
    std::string content;     // markdown result
    std::string error;       // error description if failed
};

class MessageHandler {
public:
    /**
     * Parse a two-frame ZeroMQ message into an IncomingRequest.
     */
    static IncomingRequest parse_request(const std::string& topic_frame,
                                          const std::string& payload_frame);

    /**
     * Serialize an OutgoingResponse into a JSON string for the payload frame.
     * The topic frame is response.reply_to.
     */
    static std::string serialize_response(const OutgoingResponse& response);

    /**
     * Build a success response.
     */
    static OutgoingResponse make_success(const std::string& request_id,
                                          const std::string& reply_to,
                                          const std::string& content);

    /**
     * Build an error response.
     */
    static OutgoingResponse make_error(const std::string& request_id,
                                        const std::string& reply_to,
                                        const std::string& error_msg);
};

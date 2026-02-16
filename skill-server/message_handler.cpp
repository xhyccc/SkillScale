#include "message_handler.h"
#include <iostream>
#include <chrono>

IncomingRequest MessageHandler::parse_request(const std::string& topic_frame,
                                               const std::string& payload_frame) {
    IncomingRequest req;
    req.topic = topic_frame;

    try {
        json j = json::parse(payload_frame);

        if (!j.contains("request_id") || !j.contains("reply_to") ||
            !j.contains("intent")) {
            req.parse_error = "Missing required fields (request_id, reply_to, intent)";
            return req;
        }

        req.request_id = j["request_id"].get<std::string>();
        req.reply_to   = j["reply_to"].get<std::string>();
        req.intent     = j["intent"].get<std::string>();
        req.timestamp  = j.value("timestamp", 0.0);
        req.valid      = true;

    } catch (const json::exception& e) {
        req.parse_error = std::string("JSON parse error: ") + e.what();
    }

    return req;
}

std::string MessageHandler::serialize_response(const OutgoingResponse& response) {
    json j;
    j["request_id"] = response.request_id;
    j["status"]     = response.status;
    j["content"]    = response.content;
    j["error"]      = response.error;
    j["timestamp"]  = std::chrono::duration<double>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    return j.dump();
}

OutgoingResponse MessageHandler::make_success(const std::string& request_id,
                                               const std::string& reply_to,
                                               const std::string& content) {
    return OutgoingResponse{
        .reply_to   = reply_to,
        .request_id = request_id,
        .status     = "success",
        .content    = content,
        .error      = ""
    };
}

OutgoingResponse MessageHandler::make_error(const std::string& request_id,
                                             const std::string& reply_to,
                                             const std::string& error_msg) {
    return OutgoingResponse{
        .reply_to   = reply_to,
        .request_id = request_id,
        .status     = "error",
        .content    = "",
        .error      = error_msg
    };
}

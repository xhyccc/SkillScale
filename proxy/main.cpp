/**
 * SkillScale — ZeroMQ XPUB/XSUB Proxy
 *
 * Stateless message switch that sits at the center of the star topology.
 * - Binds XSUB on port 5444  (all publishers connect here)
 * - Binds XPUB on port 5555  (all subscribers connect here)
 *
 * The proxy forwards subscription frames upstream so that messages
 * are filtered at the source (publisher), not at the proxy.
 */

#include <zmq.hpp>
#include <iostream>
#include <csignal>
#include <cstdlib>
#include <string>
#include <thread>
#include <atomic>

static std::atomic<bool> g_running{true};

static void signal_handler(int /*sig*/) {
    g_running.store(false);
}

// ──────────────────────────────────────────────────────────
//  Monitoring thread — lightweight telemetry for KEDA/Prometheus
//  Listens on the XPUB socket for subscription events and logs them.
// ──────────────────────────────────────────────────────────
static void monitor_thread(zmq::context_t& ctx,
                           const std::string& monitor_endpoint) {
    zmq::socket_t monitor(ctx, zmq::socket_type::pair);
    monitor.connect(monitor_endpoint);

    while (g_running.load()) {
        zmq::pollitem_t item = {static_cast<void*>(monitor), 0, ZMQ_POLLIN, 0};
        zmq::poll(&item, 1, std::chrono::milliseconds(500));

        if (item.revents & ZMQ_POLLIN) {
            zmq::message_t event_msg;
            auto rc = monitor.recv(event_msg, zmq::recv_flags::dontwait);
            if (rc) {
                // ZMQ monitor events are 2-frame: event_id + address
                uint16_t event_id = *reinterpret_cast<const uint16_t*>(event_msg.data());
                (void)event_id; // used for Prometheus export in production
            }
        }
    }
}

// ──────────────────────────────────────────────────────────
//  Metrics endpoint (optional, for KEDA integration)
//  Exposes simple counters over a REP socket.
// ──────────────────────────────────────────────────────────
static std::atomic<uint64_t> g_messages_forwarded{0};

static void metrics_thread(zmq::context_t& ctx, int metrics_port) {
    zmq::socket_t rep(ctx, zmq::socket_type::rep);
    rep.bind("tcp://*:" + std::to_string(metrics_port));

    while (g_running.load()) {
        zmq::pollitem_t item = {static_cast<void*>(rep), 0, ZMQ_POLLIN, 0};
        zmq::poll(&item, 1, std::chrono::milliseconds(500));

        if (item.revents & ZMQ_POLLIN) {
            zmq::message_t req;
            auto rc = rep.recv(req, zmq::recv_flags::dontwait);
            if (rc) {
                std::string body =
                    "# HELP skillscale_proxy_messages_total Total messages forwarded\n"
                    "# TYPE skillscale_proxy_messages_total counter\n"
                    "skillscale_proxy_messages_total " +
                    std::to_string(g_messages_forwarded.load()) + "\n";
                rep.send(zmq::buffer(body), zmq::send_flags::none);
            }
        }
    }
}

// ──────────────────────────────────────────────────────────
//  Custom proxy loop (instead of zmq::proxy) so we can
//  count messages and respond to SIGINT cleanly.
// ──────────────────────────────────────────────────────────
static void proxy_loop(zmq::socket_t& xsub, zmq::socket_t& xpub) {
    zmq::pollitem_t items[] = {
        {static_cast<void*>(xsub), 0, ZMQ_POLLIN, 0},
        {static_cast<void*>(xpub), 0, ZMQ_POLLIN, 0}
    };

    while (g_running.load()) {
        zmq::poll(items, 2, std::chrono::milliseconds(250));

        // XSUB → XPUB: forward published messages
        if (items[0].revents & ZMQ_POLLIN) {
            while (true) {
                zmq::message_t msg;
                auto rc = xsub.recv(msg, zmq::recv_flags::dontwait);
                if (!rc) break;

                int more = xsub.get(zmq::sockopt::rcvmore);
                xpub.send(msg,
                    more ? zmq::send_flags::sndmore : zmq::send_flags::none);
                g_messages_forwarded.fetch_add(1, std::memory_order_relaxed);
            }
        }

        // XPUB → XSUB: forward subscription frames upstream
        if (items[1].revents & ZMQ_POLLIN) {
            while (true) {
                zmq::message_t msg;
                auto rc = xpub.recv(msg, zmq::recv_flags::dontwait);
                if (!rc) break;

                int more = xpub.get(zmq::sockopt::rcvmore);
                xsub.send(msg,
                    more ? zmq::send_flags::sndmore : zmq::send_flags::none);
            }
        }
    }
}

// ──────────────────────────────────────────────────────────
//  Main
// ──────────────────────────────────────────────────────────
int main(int argc, char* argv[]) {
    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    // Configurable via environment variables (Kubernetes-friendly)
    const char* xsub_env = std::getenv("SKILLSCALE_XSUB_BIND");
    const char* xpub_env = std::getenv("SKILLSCALE_XPUB_BIND");
    const char* metrics_env = std::getenv("SKILLSCALE_METRICS_PORT");

    std::string xsub_bind = xsub_env ? xsub_env : "tcp://*:5444";
    std::string xpub_bind = xpub_env ? xpub_env : "tcp://*:5555";
    int metrics_port = metrics_env ? std::atoi(metrics_env) : 9100;

    std::cout << "[proxy] SkillScale XPUB/XSUB Proxy starting\n"
              << "[proxy]   XSUB bind: " << xsub_bind << "\n"
              << "[proxy]   XPUB bind: " << xpub_bind << "\n"
              << "[proxy]   Metrics  : tcp://*:" << metrics_port << "\n";

    zmq::context_t ctx(2);  // 2 I/O threads

    // XSUB socket — publishers connect here
    zmq::socket_t xsub(ctx, zmq::socket_type::xsub);
    xsub.set(zmq::sockopt::rcvhwm, 50000);
    xsub.set(zmq::sockopt::sndhwm, 50000);
    xsub.bind(xsub_bind);

    // XPUB socket — subscribers connect here
    zmq::socket_t xpub(ctx, zmq::socket_type::xpub);
    xpub.set(zmq::sockopt::rcvhwm, 50000);
    xpub.set(zmq::sockopt::sndhwm, 50000);
    // Enable verbose mode so duplicate subscriptions are forwarded
    xpub.set(zmq::sockopt::xpub_verbose, 1);
    xpub.bind(xpub_bind);

    std::cout << "[proxy] Sockets bound. Starting proxy loop.\n";

    // Start metrics thread
    std::thread metrics(metrics_thread, std::ref(ctx), metrics_port);

    // Run proxy in the main thread
    proxy_loop(xsub, xpub);

    std::cout << "[proxy] Shutting down...\n";
    metrics.join();

    return 0;
}

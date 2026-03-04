#include "skill_executor.h"

#include <array>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <sstream>
#include <thread>

// POSIX
#include <fcntl.h>
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>

namespace fs = std::filesystem;

SkillExecutor::SkillExecutor(int timeout_ms, const std::string& python_path)
    : timeout_ms_(timeout_ms), python_path_(python_path) {}

// ──────────────────────────────────────────────────────────
//  execute_direct — let OpenCode handle both matching & execution
//  via AGENTS.md. No explicit skill matching needed.
// ──────────────────────────────────────────────────────────

ExecutionResult SkillExecutor::execute_direct(const std::string& intent,
                                               const std::string& hint_skill) {
    std::cout << "[executor] Direct dispatch via OpenCode (AGENTS.md)\n";
    std::cout << "[executor] Intent: " << intent.substr(0, 120) << "...\n";
    if (!hint_skill.empty()) {
        std::cout << "[executor] Skill hint: " << hint_skill << "\n";
    }

    // Find the project root (where AGENTS.md and opencode.json live)
    std::string project_root;
    for (const auto& candidate : {
        std::string("."),
        std::string(".."),
        std::string("../.."),
    }) {
        if (fs::exists(candidate + "/AGENTS.md") || fs::exists(candidate + "/opencode.json")) {
            project_root = fs::absolute(candidate).string();
            break;
        }
    }

    // Find the opencode-exec wrapper script
    std::string opencode_exec;
    for (const auto& candidate : {
        std::string("./scripts/opencode-exec"),
        project_root + "/scripts/opencode-exec",
    }) {
        if (!candidate.empty() && fs::exists(candidate)) {
            opencode_exec = fs::absolute(candidate).string();
            break;
        }
    }

    if (opencode_exec.empty() || project_root.empty()) {
        std::cerr << "[executor] opencode-exec not found, cannot dispatch\n";
        ExecutionResult result;
        result.stderr_output = "opencode-exec not found (project_root=" + project_root + ")";
        return result;
    }

    // Build command — pass hint_skill as optional arg
    std::string cmd = "bash " + opencode_exec;
    if (!hint_skill.empty()) {
        cmd += " --hint " + hint_skill;
    }

    std::cout << "[executor] Running: " << cmd << "\n";
    return run_subprocess(cmd, project_root, intent);
}

ExecutionResult SkillExecutor::execute(const SkillDefinition& skill,
                                       const std::string& intent) {
    std::cout << "[executor] Executing skill: " << skill.name << "\n";
    std::cout << "[executor] Intent: " << intent.substr(0, 120) << "...\n";

    // ── Primary: Use OpenCode for intelligent AI-agent execution ──
    // OpenCode reads the project's AGENTS.md and .claude/skills/ automatically,
    // enabling intelligent skill execution with full AI agent capabilities.
    // The opencode-exec wrapper script builds the prompt and calls `opencode run`.

    // Find the project root (where AGENTS.md and opencode.json live)
    std::string project_root;
    for (const auto& candidate : {
        std::string("."),
        skill.base_dir + "/../..",
        skill.base_dir + "/../../..",
    }) {
        if (fs::exists(candidate + "/AGENTS.md") || fs::exists(candidate + "/opencode.json")) {
            project_root = fs::absolute(candidate).string();
            break;
        }
    }

    // Find the opencode-exec wrapper script
    std::string opencode_exec;
    for (const auto& candidate : {
        std::string("./scripts/opencode-exec"),
        project_root + "/scripts/opencode-exec",
        skill.base_dir + "/../../scripts/opencode-exec",
        skill.base_dir + "/../../../scripts/opencode-exec",
    }) {
        if (fs::exists(candidate)) {
            opencode_exec = fs::absolute(candidate).string();
            break;
        }
    }

    if (!opencode_exec.empty() && !project_root.empty()) {
        std::string cmd = "bash " + opencode_exec + " " + skill.name;
        if (!skill.description.empty()) {
            // Shell-escape single quotes in description
            std::string desc = skill.description;
            size_t pos = 0;
            while ((pos = desc.find('\'', pos)) != std::string::npos) {
                desc.replace(pos, 1, "'\\''");
                pos += 4;
            }
            cmd += " '" + desc + "'";
        }
        std::cout << "[executor] Using: opencode run (via opencode-exec) for " << skill.name << "\n";

        return run_subprocess(cmd, project_root, intent);
    }

    // ── Fallback 1: Use `openskills run <name>` (direct script execution) ──
    std::string openskills_bin;
    for (const auto& candidate : {
        std::string("./scripts/openskills"),
        skill.base_dir + "/../../scripts/openskills",
        skill.base_dir + "/../../../scripts/openskills",
    }) {
        if (fs::exists(candidate)) {
            openskills_bin = fs::absolute(candidate).string();
            break;
        }
    }

    if (!openskills_bin.empty()) {
        std::string cmd = "bash " + openskills_bin + " run " + skill.name;
        std::cout << "[executor] Fallback 1: openskills run " << skill.name << "\n";

        std::string env_cmd = "SKILLSCALE_PYTHON=" + python_path_ + " " + cmd;
        return run_subprocess(env_cmd, skill.base_dir, intent);
    }

    // ── Fallback 2: Direct scripts/run.py execution ──
    std::string run_py = skill.base_dir + "/scripts/run.py";
    if (fs::exists(run_py)) {
        std::cout << "[executor] Fallback 2: direct scripts/run.py execution\n";
        std::string cmd = python_path_ + " " + run_py;
        return run_subprocess(cmd, skill.base_dir, intent);
    }

    // ── Fallback 3: Direct scripts/run.sh execution ──
    std::string run_script = skill.base_dir + "/scripts/run.sh";
    if (fs::exists(run_script)) {
        std::cout << "[executor] Fallback 3: direct scripts/run.sh execution\n";
        std::string cmd = "bash " + run_script;
        return run_subprocess(cmd, skill.base_dir, intent);
    }

    // ── Fallback 4: Return raw SKILL.md instructions ──
    std::cout << "[executor] No execution method found, returning raw instructions\n";
    ExecutionResult result;
    result.success = true;
    result.exit_code = 0;
    result.stdout_output = skill.instructions;
    return result;
}

ExecutionResult SkillExecutor::run_subprocess(const std::string& command,
                                               const std::string& working_dir,
                                               const std::string& stdin_data) {
    ExecutionResult result;
    auto start_time = std::chrono::steady_clock::now();

    // Create pipes for stdout, stderr, and stdin
    int stdout_pipe[2], stderr_pipe[2], stdin_pipe[2];

    if (pipe(stdout_pipe) != 0 || pipe(stderr_pipe) != 0 || pipe(stdin_pipe) != 0) {
        result.stderr_output = "Failed to create pipes: " + std::string(strerror(errno));
        return result;
    }

    pid_t pid = fork();

    if (pid < 0) {
        result.stderr_output = "Fork failed: " + std::string(strerror(errno));
        return result;
    }

    if (pid == 0) {
        // ──── Child process ────

        // Redirect stdin
        close(stdin_pipe[1]);
        dup2(stdin_pipe[0], STDIN_FILENO);
        close(stdin_pipe[0]);

        // Redirect stdout
        close(stdout_pipe[0]);
        dup2(stdout_pipe[1], STDOUT_FILENO);
        close(stdout_pipe[1]);

        // Redirect stderr
        close(stderr_pipe[0]);
        dup2(stderr_pipe[1], STDERR_FILENO);
        close(stderr_pipe[1]);

        // Change working directory
        if (!working_dir.empty()) {
            if (chdir(working_dir.c_str()) != 0) {
                perror("chdir failed");
                _exit(127);
            }
        }

        // Set environment variable with the intent for the script
        setenv("SKILLSCALE_INTENT", stdin_data.c_str(), 1);

        // Execute via shell
        execl("/bin/sh", "sh", "-c", command.c_str(), nullptr);
        perror("execl failed");
        _exit(127);
    }

    // ──── Parent process ────

    // Close child-side pipe ends
    close(stdout_pipe[1]);
    close(stderr_pipe[1]);
    close(stdin_pipe[0]);

    // Write stdin data to child
    if (!stdin_data.empty()) {
        ssize_t written = write(stdin_pipe[1], stdin_data.c_str(), stdin_data.size());
        (void)written;
    }
    close(stdin_pipe[1]);

    // Set stdout/stderr pipes to non-blocking
    fcntl(stdout_pipe[0], F_SETFL, O_NONBLOCK);
    fcntl(stderr_pipe[0], F_SETFL, O_NONBLOCK);

    // Read from pipes with timeout monitoring
    std::string stdout_buf, stderr_buf;
    std::array<char, 4096> buf;
    bool child_done = false;
    int status = 0;

    while (!child_done) {
        // Check timeout
        auto elapsed = std::chrono::steady_clock::now() - start_time;
        if (std::chrono::duration_cast<std::chrono::milliseconds>(elapsed).count()
            > timeout_ms_) {
            std::cerr << "[executor] TIMEOUT after " << timeout_ms_
                      << "ms — sending SIGKILL to pid " << pid << "\n";
            kill(pid, SIGKILL);
            waitpid(pid, &status, 0);
            result.stderr_output = "Execution timed out after " +
                std::to_string(timeout_ms_) + "ms";
            result.exit_code = -1;
            result.elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time);
            close(stdout_pipe[0]);
            close(stderr_pipe[0]);
            return result;
        }

        // Non-blocking read stdout
        ssize_t n = read(stdout_pipe[0], buf.data(), buf.size());
        if (n > 0) stdout_buf.append(buf.data(), n);

        // Non-blocking read stderr
        n = read(stderr_pipe[0], buf.data(), buf.size());
        if (n > 0) stderr_buf.append(buf.data(), n);

        // Check if child exited
        pid_t wpid = waitpid(pid, &status, WNOHANG);
        if (wpid == pid) {
            child_done = true;
            // Drain remaining data
            while ((n = read(stdout_pipe[0], buf.data(), buf.size())) > 0)
                stdout_buf.append(buf.data(), n);
            while ((n = read(stderr_pipe[0], buf.data(), buf.size())) > 0)
                stderr_buf.append(buf.data(), n);
        } else {
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
    }

    close(stdout_pipe[0]);
    close(stderr_pipe[0]);

    result.exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
    result.success = (result.exit_code == 0);
    result.stdout_output = std::move(stdout_buf);
    result.stderr_output = std::move(stderr_buf);
    result.elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time);

    std::cout << "[executor] Finished (exit=" << result.exit_code
              << ", " << result.elapsed.count() << "ms)\n";

    return result;
}

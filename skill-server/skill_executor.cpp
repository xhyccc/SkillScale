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

SkillExecutor::SkillExecutor(int timeout_ms) : timeout_ms_(timeout_ms) {}

ExecutionResult SkillExecutor::execute(const SkillDefinition& skill,
                                       const std::string& intent) {
    std::cout << "[executor] Executing skill: " << skill.name << "\n";
    std::cout << "[executor] Intent: " << intent.substr(0, 120) << "...\n";

    // ── Strategy 1: If skill directory has scripts/run.sh, execute it ──
    std::string run_script = skill.base_dir + "/scripts/run.sh";
    if (fs::exists(run_script)) {
        std::cout << "[executor] Found scripts/run.sh, using direct execution\n";
        std::string cmd = "bash " + run_script;
        return run_subprocess(cmd, skill.base_dir, intent);
    }

    // ── Strategy 2: If skill directory has scripts/run.py, execute it ──
    std::string run_py = skill.base_dir + "/scripts/run.py";
    if (fs::exists(run_py)) {
        std::cout << "[executor] Found scripts/run.py, using Python execution\n";
        std::string cmd = "python3 " + run_py;
        return run_subprocess(cmd, skill.base_dir, intent);
    }

    // ── Strategy 3: Use OpenSkills CLI to read the skill ──
    std::string cmd = "npx openskills read " + skill.name;
    std::cout << "[executor] Using OpenSkills CLI: " << cmd << "\n";
    auto result = run_subprocess(cmd, skill.base_dir, intent);

    // If openskills CLI not available, fall back to cat the markdown
    if (!result.success && result.exit_code == 127) {
        std::cout << "[executor] openskills CLI not found, returning raw instructions\n";
        result.success = true;
        result.exit_code = 0;
        result.stdout_output = skill.instructions;
    }

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

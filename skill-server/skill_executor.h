#pragma once

#include "skill_loader.h"
#include <string>
#include <chrono>

/**
 * Result of executing a skill via subprocess.
 */
struct ExecutionResult {
    bool success = false;
    int exit_code = -1;
    std::string stdout_output;
    std::string stderr_output;
    std::chrono::milliseconds elapsed{0};
};

/**
 * Executes OpenSkills via POSIX subprocess management.
 *
 * Spawns an isolated process, captures stdout/stderr, enforces
 * timeout and memory limits via cgroup-like mechanisms.
 */
class SkillExecutor {
public:
    /// Maximum execution time before SIGKILL (milliseconds)
    explicit SkillExecutor(int timeout_ms = 30000);

    /**
     * Execute a skill with the given user intent.
     *
     * @param skill   The loaded skill definition
     * @param intent  Natural language user intent
     * @return        Execution result with captured output
     */
    ExecutionResult execute(const SkillDefinition& skill,
                            const std::string& intent);

private:
    int timeout_ms_;

    /// Execute a command, capture stdout/stderr, enforce timeout
    ExecutionResult run_subprocess(const std::string& command,
                                   const std::string& working_dir,
                                   const std::string& stdin_data = "");
};

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
    std::string matched_skill;  // skill name if detected from output
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
    explicit SkillExecutor(int timeout_ms = 30000,
                           const std::string& python_path = "python3");

    /**
     * Execute a skill with the given user intent.
     *
     * @param skill   The loaded skill definition
     * @param intent  Natural language user intent
     * @return        Execution result with captured output
     */
    ExecutionResult execute(const SkillDefinition& skill,
                            const std::string& intent);

    /**
     * Execute directly via OpenCode without pre-matching.
     * OpenCode reads AGENTS.md to decide which skill to use.
     *
     * @param intent      Natural language user intent
     * @param hint_skill  Optional skill hint (not enforced)
     * @return            Execution result with captured output
     */
    ExecutionResult execute_direct(const std::string& intent,
                                   const std::string& hint_skill = "");

private:
    int timeout_ms_;
    std::string python_path_;

    /// Execute a command, capture stdout/stderr, enforce timeout
    ExecutionResult run_subprocess(const std::string& command,
                                   const std::string& working_dir,
                                   const std::string& stdin_data = "");
};

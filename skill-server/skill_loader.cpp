#include "skill_loader.h"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <fstream>
#include <filesystem>
#include <iostream>
#include <sstream>
#include <unordered_set>

// POSIX
#include <sys/wait.h>
#include <unistd.h>

namespace fs = std::filesystem;

SkillLoader::SkillLoader(const std::string& skills_dir)
    : skills_dir_(skills_dir) {}

// ──────────────────────────────────────────────────────────
//  load_all — OpenSkills-first, fallback to recursive scan
// ──────────────────────────────────────────────────────────

int SkillLoader::load_all() {
    if (!fs::exists(skills_dir_)) {
        std::cerr << "[loader] Skills directory does not exist: "
                  << skills_dir_ << "\n";
        return 0;
    }

    // ── Strategy 1: OpenSkills — parse AGENTS.md for discovery ──
    std::string agents_md = skills_dir_ + "/AGENTS.md";
    if (fs::exists(agents_md)) {
        int count = load_from_agents_md(agents_md);
        if (count > 0) {
            std::cout << "[loader] OpenSkills: discovered " << count
                      << " skills from AGENTS.md\n";
            return count;
        }
    }

    // ── Strategy 2: Legacy — recursive scan for SKILL.md files ──
    std::cout << "[loader] No AGENTS.md found, falling back to recursive SKILL.md scan\n";
    int count = 0;

    for (auto& entry : fs::recursive_directory_iterator(skills_dir_)) {
        if (!entry.is_regular_file()) continue;
        if (entry.path().filename() != "SKILL.md") continue;

        SkillDefinition skill;
        if (parse_skill_md(entry.path().string(), skill)) {
            std::cout << "[loader] Loaded skill: " << skill.name
                      << " from " << entry.path().string() << "\n";
            skill.details_loaded = true;
            skills_[skill.name] = std::move(skill);
            ++count;
        }
    }

    std::cout << "[loader] Total skills loaded: " << count << "\n";
    return count;
}

// ──────────────────────────────────────────────────────────
//  load_from_agents_md — parse <available_skills> XML block
// ──────────────────────────────────────────────────────────

int SkillLoader::load_from_agents_md(const std::string& agents_md_path) {
    std::ifstream file(agents_md_path);
    if (!file.is_open()) {
        std::cerr << "[loader] Cannot open AGENTS.md: " << agents_md_path << "\n";
        return 0;
    }

    std::stringstream buf;
    buf << file.rdbuf();
    std::string content = buf.str();

    // Find <available_skills> ... </available_skills> block
    auto block_start = content.find("<available_skills>");
    auto block_end   = content.find("</available_skills>");
    if (block_start == std::string::npos || block_end == std::string::npos) {
        std::cerr << "[loader] No <available_skills> block in AGENTS.md\n";
        return 0;
    }

    std::string block = content.substr(
        block_start + 18,  // len("<available_skills>")
        block_end - block_start - 18);

    // Parse each <skill> ... </skill> entry
    int count = 0;
    std::string::size_type pos = 0;

    while (true) {
        auto skill_start = block.find("<skill>", pos);
        if (skill_start == std::string::npos) break;

        auto skill_end = block.find("</skill>", skill_start);
        if (skill_end == std::string::npos) break;

        std::string skill_xml = block.substr(
            skill_start + 7,  // len("<skill>")
            skill_end - skill_start - 7);

        std::string name = extract_xml_tag(skill_xml, "name");
        std::string desc = extract_xml_tag(skill_xml, "description");
        std::string loc  = extract_xml_tag(skill_xml, "location");

        if (name.empty()) {
            pos = skill_end + 8;
            continue;
        }

        SkillDefinition skill;
        skill.name = name;
        skill.description = desc;

        // Resolve skill base directory from location
        fs::path base = fs::path(skills_dir_) / loc;
        if (fs::exists(base)) {
            skill.base_dir = fs::absolute(base).string();
        } else {
            // Try without trailing slash
            std::string loc_clean = loc;
            while (!loc_clean.empty() && loc_clean.back() == '/')
                loc_clean.pop_back();
            base = fs::path(skills_dir_) / loc_clean;
            skill.base_dir = fs::absolute(base).string();
        }

        // Check if SKILL.md exists at this location
        fs::path skill_md_path = base / "SKILL.md";
        if (fs::exists(skill_md_path)) {
            skill.file_path = fs::absolute(skill_md_path).string();
        }

        skill.details_loaded = false;  // Progressive disclosure: loaded on demand

        std::cout << "[loader] Discovered skill: " << skill.name
                  << " (base=" << skill.base_dir << ")\n";

        skills_[skill.name] = std::move(skill);
        ++count;
        pos = skill_end + 8;  // len("</skill>")
    }

    return count;
}

// ──────────────────────────────────────────────────────────
//  load_skill_details — progressive disclosure via CLI
// ──────────────────────────────────────────────────────────

bool SkillLoader::load_skill_details(SkillDefinition& skill) {
    if (skill.details_loaded) return true;

    std::cout << "[loader] Progressive disclosure: loading SKILL.md for '"
              << skill.name << "'\n";

    // ── Strategy 1: Try `openskills read <name>` CLI (OpenSkills protocol) ──
    std::string cli_output;
    // Use local openskills script; set SKILLSCALE_SKILLS_DIR for skill lookup
    std::string cmd = "SKILLSCALE_SKILLS_DIR=\"" + skills_dir_ + "\" "
                      + skills_dir_ + "/../scripts/openskills read "
                      + skill.name + " 2>/dev/null";
    int rc = run_command(cmd, cli_output);

    if (rc == 0 && !cli_output.empty()) {
        std::cout << "[loader] Loaded via openskills CLI (" << cli_output.size()
                  << " bytes)\n";
        skill.instructions = cli_output;
        skill.details_loaded = true;
        return true;
    }

    // ── Strategy 2: Read SKILL.md file directly ──
    if (!skill.file_path.empty() && fs::exists(skill.file_path)) {
        if (parse_skill_md(skill.file_path, skill)) {
            skill.details_loaded = true;
            std::cout << "[loader] Loaded SKILL.md directly from "
                      << skill.file_path << "\n";
            return true;
        }
    }

    // ── Strategy 3: Try to find SKILL.md in base_dir ──
    if (!skill.base_dir.empty()) {
        std::string fallback_path = skill.base_dir + "/SKILL.md";
        if (fs::exists(fallback_path)) {
            if (parse_skill_md(fallback_path, skill)) {
                skill.details_loaded = true;
                std::cout << "[loader] Loaded SKILL.md from base_dir: "
                          << fallback_path << "\n";
                return true;
            }
        }
    }

    std::cerr << "[loader] WARNING: Could not load details for skill '"
              << skill.name << "'\n";
    return false;
}

const SkillDefinition* SkillLoader::find(const std::string& name) const {
    // Try exact match first
    auto it = skills_.find(name);
    if (it != skills_.end()) return &it->second;

    // Case-insensitive fallback
    std::string lower_name = name;
    std::transform(lower_name.begin(), lower_name.end(),
                   lower_name.begin(), ::tolower);

    for (auto& [key, val] : skills_) {
        std::string lower_key = key;
        std::transform(lower_key.begin(), lower_key.end(),
                       lower_key.begin(), ::tolower);
        if (lower_key == lower_name) return &val;
    }

    return nullptr;
}

bool SkillLoader::parse_skill_md(const std::string& path,
                                 SkillDefinition& out) {
    std::ifstream file(path);
    if (!file.is_open()) {
        std::cerr << "[loader] Cannot open: " << path << "\n";
        return false;
    }

    std::stringstream buf;
    buf << file.rdbuf();
    std::string content = buf.str();

    // Locate YAML frontmatter delimiters ---
    auto first_delim = content.find("---");
    if (first_delim == std::string::npos) {
        std::cerr << "[loader] No frontmatter in: " << path << "\n";
        return false;
    }

    auto second_delim = content.find("---", first_delim + 3);
    if (second_delim == std::string::npos) {
        std::cerr << "[loader] Unterminated frontmatter in: " << path << "\n";
        return false;
    }

    std::string yaml = content.substr(first_delim + 3,
                                      second_delim - first_delim - 3);
    std::string body = content.substr(second_delim + 3);

    // Parse required fields from YAML (simple key: value parser)
    out.name = extract_frontmatter_value(yaml, "name");
    out.description = extract_frontmatter_value(yaml, "description");
    out.license = extract_frontmatter_value(yaml, "license");
    out.compatibility = extract_frontmatter_value(yaml, "compatibility");

    // Parse allowed-tools as space-delimited list
    std::string tools_str = extract_frontmatter_value(yaml, "allowed-tools");
    if (!tools_str.empty()) {
        std::istringstream ts(tools_str);
        std::string tool;
        while (ts >> tool) {
            out.allowed_tools.push_back(tool);
        }
    }

    if (out.name.empty()) {
        std::cerr << "[loader] Skill has no name in: " << path << "\n";
        return false;
    }

    out.file_path = fs::absolute(path).string();
    out.instructions = body;
    out.base_dir = fs::absolute(fs::path(path).parent_path()).string();

    return true;
}

std::string SkillLoader::extract_frontmatter_value(
    const std::string& yaml, const std::string& key) const {

    // Look for "key:" at the start of a line
    std::string search = key + ":";
    auto pos = yaml.find(search);
    if (pos == std::string::npos) return "";

    // Check it's at line start
    if (pos > 0 && yaml[pos - 1] != '\n') {
        // Try again from next occurrence
        pos = yaml.find("\n" + search);
        if (pos == std::string::npos) return "";
        pos += 1; // skip the \n
    }

    auto value_start = pos + search.size();
    auto line_end = yaml.find('\n', value_start);
    std::string value = yaml.substr(value_start,
        line_end == std::string::npos ? std::string::npos : line_end - value_start);

    // Trim whitespace and quotes
    auto trim = [](std::string& s) {
        s.erase(0, s.find_first_not_of(" \t\"'"));
        s.erase(s.find_last_not_of(" \t\"'\r") + 1);
    };
    trim(value);
    return value;
}

// ──────────────────────────────────────────────────────────
//  extract_xml_tag — simple XML tag content extraction
// ──────────────────────────────────────────────────────────

std::string SkillLoader::extract_xml_tag(const std::string& xml,
                                          const std::string& tag) {
    std::string open_tag  = "<" + tag + ">";
    std::string close_tag = "</" + tag + ">";

    auto start = xml.find(open_tag);
    if (start == std::string::npos) return "";

    auto content_start = start + open_tag.size();
    auto end = xml.find(close_tag, content_start);
    if (end == std::string::npos) return "";

    std::string content = xml.substr(content_start, end - content_start);

    // Trim whitespace
    auto first = content.find_first_not_of(" \t\n\r");
    if (first == std::string::npos) return "";
    auto last = content.find_last_not_of(" \t\n\r");
    return content.substr(first, last - first + 1);
}

// ──────────────────────────────────────────────────────────
//  run_command — capture stdout from a subprocess
// ──────────────────────────────────────────────────────────

int SkillLoader::run_command(const std::string& cmd, std::string& output) {
    output.clear();

    int pipefd[2];
    if (pipe(pipefd) != 0) return -1;

    pid_t pid = fork();
    if (pid < 0) {
        close(pipefd[0]);
        close(pipefd[1]);
        return -1;
    }

    if (pid == 0) {
        // Child
        close(pipefd[0]);
        dup2(pipefd[1], STDOUT_FILENO);
        close(pipefd[1]);
        execl("/bin/sh", "sh", "-c", cmd.c_str(), nullptr);
        _exit(127);
    }

    // Parent
    close(pipefd[1]);
    char buf[4096];
    ssize_t n;
    while ((n = read(pipefd[0], buf, sizeof(buf))) > 0) {
        output.append(buf, n);
    }
    close(pipefd[0]);

    int status;
    waitpid(pid, &status, 0);
    return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}

// ──────────────────────────────────────────────────────────
//  Description-based skill matching (Mode 2)
// ──────────────────────────────────────────────────────────

std::vector<std::string> SkillLoader::tokenize(const std::string& text) {
    std::vector<std::string> tokens;
    std::string word;

    for (char c : text) {
        if (std::isalnum(static_cast<unsigned char>(c))) {
            word += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        } else {
            if (!word.empty()) {
                tokens.push_back(word);
                word.clear();
            }
        }
    }
    if (!word.empty()) tokens.push_back(word);

    return tokens;
}

int SkillLoader::keyword_score(const std::vector<std::string>& text_tokens,
                               const std::vector<std::string>& keyword_tokens) {
    // Stopwords to skip during scoring
    static const std::unordered_set<std::string> stopwords = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "can", "shall",
        "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "as", "into", "through", "during", "before", "after", "and",
        "but", "or", "nor", "not", "so", "yet", "both", "either",
        "neither", "each", "every", "all", "any", "few", "more",
        "most", "other", "some", "such", "no", "only", "own",
        "same", "than", "too", "very", "just", "because", "it",
        "its", "this", "that", "these", "those", "i", "me", "my",
        "we", "our", "you", "your", "he", "she", "they", "them",
        "what", "which", "who", "whom", "how", "when", "where", "why",
        "if", "then", "else", "about", "up", "out", "off", "over",
        "under", "again", "further", "once", "here", "there", "also",
        "please", "need", "want", "help", "using",
    };

    std::unordered_set<std::string> text_set;
    for (auto& t : text_tokens) {
        if (stopwords.find(t) == stopwords.end())
            text_set.insert(t);
    }

    int score = 0;
    for (auto& kw : keyword_tokens) {
        if (stopwords.find(kw) != stopwords.end()) continue;

        // Exact match
        if (text_set.count(kw)) {
            score += 3;
            continue;
        }
        // Substring match (e.g. "summariz" in "summarize")
        for (auto& t : text_set) {
            if (t.find(kw) != std::string::npos || kw.find(t) != std::string::npos) {
                score += 1;
                break;
            }
        }
    }
    return score;
}

const SkillDefinition* SkillLoader::match_by_description(
    const std::string& task_text) const {

    if (skills_.empty()) return nullptr;

    auto text_tokens = tokenize(task_text);
    if (text_tokens.empty()) return nullptr;

    const SkillDefinition* best = nullptr;
    int best_score = 0;

    for (auto& [name, skill] : skills_) {
        // Build keyword pool from skill name + description
        auto name_tokens = tokenize(name);
        auto desc_tokens = tokenize(skill.description);

        // Combine
        std::vector<std::string> all_keywords;
        all_keywords.insert(all_keywords.end(),
                            name_tokens.begin(), name_tokens.end());
        all_keywords.insert(all_keywords.end(),
                            desc_tokens.begin(), desc_tokens.end());

        int score = keyword_score(text_tokens, all_keywords);

        std::cout << "[loader] Matching '" << name << "': score=" << score << "\n";

        if (score > best_score) {
            best_score = score;
            best = &skill;
        }
    }

    if (best) {
        std::cout << "[loader] Best match: " << best->name
                  << " (score=" << best_score << ")\n";
    }

    return best;
}

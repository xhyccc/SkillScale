#include "skill_loader.h"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <filesystem>
#include <iostream>
#include <sstream>
#include <unordered_set>

namespace fs = std::filesystem;

SkillLoader::SkillLoader(const std::string& skills_dir)
    : skills_dir_(skills_dir) {}

int SkillLoader::load_all() {
    int count = 0;

    if (!fs::exists(skills_dir_)) {
        std::cerr << "[loader] Skills directory does not exist: "
                  << skills_dir_ << "\n";
        return 0;
    }

    for (auto& entry : fs::recursive_directory_iterator(skills_dir_)) {
        if (!entry.is_regular_file()) continue;
        if (entry.path().filename() != "SKILL.md") continue;

        SkillDefinition skill;
        if (parse_skill_md(entry.path().string(), skill)) {
            std::cout << "[loader] Loaded skill: " << skill.name
                      << " from " << entry.path().string() << "\n";
            skills_[skill.name] = std::move(skill);
            ++count;
        }
    }

    std::cout << "[loader] Total skills loaded: " << count << "\n";
    return count;
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

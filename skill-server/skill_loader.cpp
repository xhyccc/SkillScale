#include "skill_loader.h"

#include <algorithm>
#include <fstream>
#include <filesystem>
#include <iostream>
#include <sstream>

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

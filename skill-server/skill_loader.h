#pragma once

#include <string>
#include <vector>
#include <unordered_map>

/**
 * Represents a parsed SKILL.md file — YAML frontmatter + markdown body.
 */
struct SkillDefinition {
    std::string name;
    std::string description;
    std::string license;
    std::string compatibility;
    std::vector<std::string> allowed_tools;

    // Full path to the SKILL.md file on disk
    std::string file_path;

    // The markdown body (everything after the frontmatter)
    std::string instructions;

    // Base directory of the skill (contains scripts/, references/, etc.)
    std::string base_dir;
};

/**
 * Loads SKILL.md files from a directory tree.
 *
 * Scans `skills_dir` recursively for files named SKILL.md, parses the
 * YAML frontmatter, and returns a map keyed by skill name.
 */
class SkillLoader {
public:
    explicit SkillLoader(const std::string& skills_dir);

    /// Parse all skills; returns count loaded
    int load_all();

    /// Lookup a skill by name (case-insensitive match)
    const SkillDefinition* find(const std::string& name) const;

    /// All loaded skills
    const std::unordered_map<std::string, SkillDefinition>& skills() const {
        return skills_;
    }

private:
    std::string skills_dir_;
    std::unordered_map<std::string, SkillDefinition> skills_;

    bool parse_skill_md(const std::string& path, SkillDefinition& out);
    std::string extract_frontmatter_value(const std::string& yaml,
                                          const std::string& key) const;
};

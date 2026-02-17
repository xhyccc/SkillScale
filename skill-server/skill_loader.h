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

    // Whether full SKILL.md details have been loaded (progressive disclosure)
    bool details_loaded = false;
};

/**
 * Loads skills using the OpenSkills invocation flow:
 *
 *   1. Parse AGENTS.md `<available_skills>` for lightweight discovery
 *   2. Match incoming tasks against skill descriptions (keyword scoring)
 *   3. Progressive disclosure: load full SKILL.md on demand via CLI
 *   4. Execute scripts/run.py for the matched skill
 *
 * Also supports legacy mode: scan directory for SKILL.md files directly.
 */
class SkillLoader {
public:
    explicit SkillLoader(const std::string& skills_dir);

    /// Parse all skills; returns count loaded.
    /// Prefers AGENTS.md (OpenSkills discovery) if present,
    /// otherwise falls back to recursive SKILL.md scanning.
    int load_all();

    /// Parse AGENTS.md <available_skills> block for lightweight discovery.
    /// Returns count of skills discovered.
    int load_from_agents_md(const std::string& agents_md_path);

    /// Progressive disclosure: load full SKILL.md for a skill on demand.
    /// Uses `openskills read <name>` CLI if available, else reads file directly.
    /// Returns true if details were loaded successfully.
    bool load_skill_details(SkillDefinition& skill);

    /// Lookup a skill by name (case-insensitive match)
    const SkillDefinition* find(const std::string& name) const;

    /// Match a plain-text task description against installed skill
    /// descriptions using keyword scoring. Returns the best match or
    /// nullptr if no skills are loaded.
    const SkillDefinition* match_by_description(const std::string& task_text) const;

    /// All loaded skills (mutable access for progressive loading)
    std::unordered_map<std::string, SkillDefinition>& skills() {
        return skills_;
    }

    /// All loaded skills (const access)
    const std::unordered_map<std::string, SkillDefinition>& skills() const {
        return skills_;
    }

private:
    std::string skills_dir_;
    std::unordered_map<std::string, SkillDefinition> skills_;

    bool parse_skill_md(const std::string& path, SkillDefinition& out);
    std::string extract_frontmatter_value(const std::string& yaml,
                                          const std::string& key) const;

    /// Extract XML tag content: <tag>content</tag>
    static std::string extract_xml_tag(const std::string& xml,
                                       const std::string& tag);

    /// Run a subprocess command and capture stdout. Returns exit code.
    static int run_command(const std::string& cmd, std::string& output);

    /// Tokenize a string into lowercase words
    static std::vector<std::string> tokenize(const std::string& text);

    /// Score how well `text` matches `keywords`
    static int keyword_score(const std::vector<std::string>& text_tokens,
                             const std::vector<std::string>& keyword_tokens);
};

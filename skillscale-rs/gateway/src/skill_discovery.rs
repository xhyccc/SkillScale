use std::fs;
use std::path::Path;
use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentDef {
    pub category: String,
    pub description: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillDef {
    pub category: String,
    pub name: String,
    pub description: String,
}

pub fn discover_agents(skills_root: &Path) -> Vec<AgentDef> {
    let mut agents = Vec::new();

    if let Ok(entries) = fs::read_dir(skills_root) {
        for entry in entries.filter_map(Result::ok) {
            let path = entry.path();
            if path.is_dir() {
                // Assume directory name is the category
                let category = path.file_name()
                    .and_then(|n| n.to_str())
                    .unwrap_or("unknown")
                    .to_string();

                let agents_path = path.join("AGENTS.md");
                if agents_path.exists() {
                    if let Ok(content) = fs::read_to_string(agents_path) {
                        let description = extract_agent_description(&content);
                        agents.push(AgentDef {
                            category,
                            description,
                        });
                    }
                }
            }
        }
    }

    agents
}

pub fn discover_skills(skills_root: &Path) -> Vec<SkillDef> {
    let mut skills = Vec::new();

    if let Ok(entries) = fs::read_dir(skills_root) {
        for entry in entries.filter_map(Result::ok) {
            let path = entry.path();
            if path.is_dir() {
                // Assume directory name is the category
                let category = path.file_name()
                    .and_then(|n| n.to_str())
                    .unwrap_or("unknown")
                    .to_string();

                let agents_path = path.join("AGENTS.md");
                if agents_path.exists() {
                    if let Ok(content) = fs::read_to_string(agents_path) {
                        let parsed_skills = parse_agents_md(&category, &content);
                        skills.extend(parsed_skills);
                    }
                }
            }
        }
    }

    skills
}

fn parse_agents_md(category: &str, content: &str) -> Vec<SkillDef> {
    let mut skills = Vec::new();
    
    let v: Vec<&str> = content.split("<skill>").collect();
    
    for chunk in v.iter().skip(1) {
        if let Some(end_idx) = chunk.find("</skill>") {
            let skill_block = &chunk[0..end_idx];
            
            let name = extract_tag(skill_block, "name");
            let description = extract_tag(skill_block, "description");
            
            if let (Some(name), Some(description)) = (name, description) {
                skills.push(SkillDef {
                    category: category.to_string(),
                    name: name.trim().to_string(),
                    description: description.trim().to_string(),
                });
            }
        }
    }
    
    skills
}

fn extract_tag(content: &str, tag_name: &str) -> Option<String> {
    let open_tag = format!("<{}>", tag_name);
    let close_tag = format!("</{}>", tag_name);
    
    if let Some(start) = content.find(&open_tag) {
        if let Some(end) = content.find(&close_tag) {
            if start + open_tag.len() < end {
                return Some(content[start + open_tag.len()..end].to_string());
            }
        }
    }
    None
}

fn extract_agent_description(content: &str) -> String {
    // Extract everything before the first "##" or "<available_skills>"
    let mut desc = String::new();
    for line in content.lines() {
        if line.starts_with("##") || line.starts_with("<available_skills>") {
            break;
        }
        if !line.starts_with("#") && !line.trim().is_empty() {
            desc.push_str(line);
            desc.push('\n');
        }
    }
    let trimmed = desc.trim().to_string();
    if trimmed.is_empty() {
        "AI Agent".to_string()
    } else {
        trimmed
    }
}

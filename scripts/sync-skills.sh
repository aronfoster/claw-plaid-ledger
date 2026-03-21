#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

OPENCLAW_AGENTS="${OPENCLAW_AGENTS:-$HOME/.openclaw/workspace/agents}"

declare -A SKILLS=(
  [athena-ledger]="athena"
  [hestia-ledger]="hestia"
)

usage() {
  echo "Usage: $0 <push|pull>"
  echo ""
  echo "  push  Copy skill definitions from this repo into the openclaw agent directories"
  echo "  pull  Copy skill definitions from the openclaw agent directories back into this repo"
  exit 1
}

# Parse SKILL.md frontmatter and return a field value using python3.
# Usage: parse_skill_field <skill_md_path> <field>
# field is one of: name, description, env
parse_skill_field() {
  local skill_md="$1"
  local field="$2"
  python3 - "$skill_md" "$field" <<'PYEOF'
import sys, re

skill_md = sys.argv[1]
field = sys.argv[2]

with open(skill_md) as f:
    content = f.read()

# Extract YAML frontmatter between --- delimiters
m = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
if not m:
    sys.exit(0)

fm = m.group(1)

if field == "name":
    nm = re.search(r'^name:\s*(.+)$', fm, re.MULTILINE)
    print(nm.group(1).strip() if nm else "")

elif field == "description":
    dm = re.search(r'^description:\s*(.+)$', fm, re.MULTILINE)
    print(dm.group(1).strip() if dm else "")

elif field == "env":
    # Find metadata.openclaw.requires.env list
    # Look for the env block under requires:
    env_block = re.search(r'requires:\s*\n((?:\s+.*\n)*)', fm)
    if not env_block:
        sys.exit(0)
    requires_section = env_block.group(1)
    env_match = re.search(r'env:\s*\n((?:\s+-\s+\S+\n?)+)', requires_section)
    if not env_match:
        sys.exit(0)
    items = re.findall(r'-\s+(\S+)', env_match.group(1))
    print(" ".join(items))
PYEOF
}

# Upsert the ## Skills sentinel block in the target agent's TOOLS.md.
# Usage: upsert_tools_md <agent_dir> <skill_name> <description> <skill_path> <env_vars>
upsert_tools_md() {
  local agent_dir="$1"
  local skill_name="$2"
  local description="$3"
  local skill_path="$4"
  local env_vars="$5"

  local tools_md="$agent_dir/TOOLS.md"

  # Format the required env as backtick-quoted comma-separated list
  local env_formatted
  env_formatted=$(python3 - "$env_vars" <<'PYEOF'
import sys
vars_str = sys.argv[1].strip()
if not vars_str:
    print("")
else:
    items = vars_str.split()
    print(", ".join(f"`{v}`" for v in items))
PYEOF
)

  # Build the new entry for this skill
  local new_entry
  new_entry="### ${skill_name}
- **Description:** ${description}
- **Skill path:** ${skill_path}
- **Required env:** ${env_formatted}"

  if [[ ! -f "$tools_md" ]]; then
    # Create a new TOOLS.md with only the injected block
    cat > "$tools_md" <<EOF
## Skills (managed by sync-skills.sh — do not edit between markers)

<!-- sync-skills-start -->
${new_entry}
<!-- sync-skills-end -->
EOF
    echo "tools-md: created $tools_md"
    return
  fi

  # File exists — check if sentinel block is present
  if grep -q "<!-- sync-skills-start -->" "$tools_md"; then
    # Extract existing block content between sentinels
    local existing_block
    existing_block=$(python3 - "$tools_md" "$skill_name" "$new_entry" <<'PYEOF'
import sys, re

tools_md = sys.argv[1]
skill_name = sys.argv[2]
new_entry = sys.argv[3]

with open(tools_md) as f:
    content = f.read()

# Find sentinel block
start_marker = "<!-- sync-skills-start -->"
end_marker = "<!-- sync-skills-end -->"
start_idx = content.find(start_marker)
end_idx = content.find(end_marker)

if start_idx == -1 or end_idx == -1:
    print(content)
    sys.exit(0)

inside = content[start_idx + len(start_marker):end_idx]

# Check if this skill already has an entry (heading ### skill_name)
skill_heading = f"### {skill_name}"
skill_pattern = re.compile(
    r'(### ' + re.escape(skill_name) + r'\n(?:(?!### ).+\n?)*)',
    re.MULTILINE
)

if skill_pattern.search(inside):
    # Replace the existing entry
    new_inside = skill_pattern.sub(new_entry + "\n", inside)
else:
    # Append the new entry
    new_inside = inside.rstrip("\n") + "\n" + new_entry + "\n"

new_content = (
    content[:start_idx + len(start_marker)]
    + new_inside
    + content[end_idx:]
)
print(new_content, end="")
PYEOF
)
    printf '%s' "$existing_block" > "$tools_md"
    echo "tools-md: updated $tools_md (skill=${skill_name})"
  else
    # No sentinel block — append it at the end of the file
    {
      echo ""
      echo "## Skills (managed by sync-skills.sh — do not edit between markers)"
      echo ""
      echo "<!-- sync-skills-start -->"
      echo "${new_entry}"
      echo "<!-- sync-skills-end -->"
    } >> "$tools_md"
    echo "tools-md: appended skills block to $tools_md"
  fi
}

cmd_push() {
  for skill in "${!SKILLS[@]}"; do
    agent="${SKILLS[$skill]}"
    src="$REPO_ROOT/skills/$skill/"
    dst="$OPENCLAW_AGENTS/$agent/skills/$skill/"
    mkdir -p "$dst"
    rsync -a --delete "$src" "$dst"
    echo "push: $skill -> $dst"

    # Parse frontmatter fields from the skill's SKILL.md
    local skill_md="$src/SKILL.md"
    if [[ ! -f "$skill_md" ]]; then
      echo "push: WARNING — $skill_md not found, skipping TOOLS.md update" >&2
      continue
    fi

    local skill_name description env_vars
    skill_name=$(parse_skill_field "$skill_md" name)
    description=$(parse_skill_field "$skill_md" description)
    env_vars=$(parse_skill_field "$skill_md" env)

    local agent_dir="$OPENCLAW_AGENTS/$agent"
    local skill_path="~/.openclaw/workspace/agents/$agent/skills/$skill/SKILL.md"

    upsert_tools_md "$agent_dir" "$skill_name" "$description" "$skill_path" "$env_vars"
  done
}

cmd_pull() {
  for skill in "${!SKILLS[@]}"; do
    agent="${SKILLS[$skill]}"
    src="$OPENCLAW_AGENTS/$agent/skills/$skill/"
    dst="$REPO_ROOT/skills/$skill/"
    if [[ ! -d "$src" ]]; then
      echo "pull: $src not found, skipping $skill" >&2
      continue
    fi
    rsync -a --delete "$src" "$dst"
    echo "pull: $skill <- $src"
  done
}

[[ $# -eq 1 ]] || usage

case "$1" in
  push) cmd_push ;;
  pull) cmd_pull ;;
  *) usage ;;
esac

from nexus.skills.loader import DeclarativeSkill, SkillLoader, SkillRegistry


def test_skill_loader_markdown_parsing(tmp_path):
    registry = SkillRegistry()
    loader = SkillLoader(registry, working_dir=tmp_path, trusted=lambda x: True)

    # Create project skills dir
    skill_dir = tmp_path / ".nexus" / "skills"
    skill_dir.mkdir(parents=True)

    skill_md = skill_dir / "my-skill.md"
    skill_md.write_text("""---
name: awesome-skill
description: this is awesome
keywords: awesome, great
---
This is the prompt text.

## Quality Checklist
- Make sure it works
- Do not break things
""")

    loader.load_project()

    skill = registry.get("awesome-skill")
    assert skill is not None
    assert skill.description == "this is awesome"
    assert skill.trigger.keywords == ["awesome", "great"]
    assert "This is the prompt text." in skill.get_system_prompt()

    checklist = skill.get_quality_checklist()
    assert len(checklist) == 2
    assert checklist[0] == "Make sure it works"


def test_skill_loader_auto_activate():
    registry = SkillRegistry()
    skill1 = DeclarativeSkill(
        name="react-skill", description="desc", prompt="", checklist=[], keywords=["react", "jsx"]
    )
    registry.register(skill1)

    activated = registry.auto_activate(
        user_input="Can you write a react component for this?", file_path="app.jsx"
    )
    assert len(activated) == 1
    assert activated[0].name == "react-skill"

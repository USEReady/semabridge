# Copilot Instructions

This repository uses agent skills stored in [skills](skills). Before taking action, determine which skill applies and follow it.

## Skill Workflow

- Use [using-agent-skills](skills/using-agent-skills/SKILL.md) at the start of each task to select the right workflow.
- Use [idea-refine](skills/idea-refine/SKILL.md) for vague or underspecified requests.
- Use [spec-driven-development](skills/spec-driven-development/SKILL.md) before implementing new features or significant changes.
- Use [planning-and-task-breakdown](skills/planning-and-task-breakdown/SKILL.md) to break larger work into ordered steps.
- Use [incremental-implementation](skills/incremental-implementation/SKILL.md) when writing or modifying code.
- Use [test-driven-development](skills/test-driven-development/SKILL.md) when adding logic or fixing bugs.
- Use [debugging-and-error-recovery](skills/debugging-and-error-recovery/SKILL.md) when behavior fails or tests/builds break.
- Use [code-review-and-quality](skills/code-review-and-quality/SKILL.md) before merging or shipping changes.
- Use [shipping-and-launch](skills/shipping-and-launch/SKILL.md) for release/deployment work.
- Use [ci-cd-and-automation](skills/ci-cd-and-automation/SKILL.md) for pipeline and automation work.
- Use [documentation-and-adrs](skills/documentation-and-adrs/SKILL.md) when recording decisions or updating docs.

## Workflows

These are multi-step sequential processes:

- /ideate (`idea-refine`)
- /spec (`spec-driven-development`)
- /plan (`planning-and-task-breakdown`)
- /implement (`incremental-implementation`)
- /tdd (`test-driven-development`)
- /debug (`debugging-and-error-recovery`)
- /review (`code-review-and-quality`)
- /launch (`shipping-and-launch`)
- /ci-cd (`ci-cd-and-automation`)

## Operating Rules

- Prefer the smallest change that satisfies the request.
- Read the relevant source files before editing.
- Validate changes with tests or another concrete verification step.
- Do not edit generated outputs when the source generator is available.
- Surface assumptions when requirements are unclear.

## Repository Notes

- This codebase is workflow-driven; skill selection is part of the implementation process, not optional context gathering.
- When a task clearly matches a skill, follow that skill first instead of improvising a custom workflow.
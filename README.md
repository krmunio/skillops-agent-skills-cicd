# Self-Evolving Agent SkillOps

[English](README.md) | [한국어](README.ko.md)

**CI/CD for agent behavior. SkillOps proposes. Evidence informs. People approve.**

Self-Evolving Agent SkillOps uses AI agents to assess reusable skills, propose
revised instructions, and evaluate their effects in a project. Through bounded
feedback-driven iteration, evaluation findings can inform the next
candidate—evolving the instruction bundle, not the model's weights.

"Self-evolving" means agent-driven proposal and evaluation, not silent rewriting
of active skills or automatic production deployment. **An explicit human approval
gate separates candidate evaluation from adoption.**

## Watch the demo — 1 minute 50 seconds

[![Watch the SkillOps demo: instruction quality and project execution inform human review](docs/media/skillops-demo-cover.jpg)](docs/media/skillops-demo-en.mp4)

[Watch or download the demo (MP4)](docs/media/skillops-demo-en.mp4) ·
English narration and burned-in English subtitles · 110 seconds

*Recorded September 21, 2026. The video reviews saved evidence, not a new
evaluation or approval. Its "GEPA not integrated" statement describes that
version; the current code supports [optional GEPA optimization](docs/GEPA.md).*

## Problem Statement

A better-written skill does not necessarily produce better results. Higher
instruction-quality scores may leave generated code unchanged, existing checks
failing, and execution consuming more time and resources.

The evidence is often scattered across instruction diffs, evaluation reports,
and execution logs. Teams need a clear way to determine what changed, whether
it helped in their project, and whether the candidate is ready for adoption.

## Solution

SkillOps connects four activities in an agent-driven improvement workflow:

- **Assess:** discover versioned skill bundles and evaluate instruction quality
  against explicit criteria, such as those derived from Anthropic's
  skill-authoring guidance, with planned support for adding other
  instruction-quality evaluation sets.
- **Propose:** an AI agent uses assessment findings and available project-check
  results to generate revised instructions, addressed findings, and an
  improvement hypothesis without overwriting the original.
- **Compare and iterate:** evaluate the candidate against the original using
  instruction assessments and supported project checks. An opt-in development
  loop feeds evaluation results into subsequent agent-generated candidates
  within explicit round, invocation, and runtime limits.
- **Review and approve:** inspect the evidence through a read-only dashboard,
  then use separate local controls for confirmation and human approval.
  An approval gate separates evaluation from adoption; subsequent execution
  requires its own permission.

Candidate lineage, evaluation feedback, and stopping reasons remain traceable.
Missing checks and incomplete verification remain **unverified**, not successful.

## Business Value

SkillOps helps skill authors automate repeated assessment and revision,
developers inspect execution trade-offs, and reviewers make adoption decisions
with connected evidence.

The recorded comparison in the demo illustrates the need: the candidate received
a higher instruction-quality score but generated identical code, retained the
same two failing checks, and consumed more time and recorded usage. Its stored
decision remained **improved**—showing why reviewers need the evidence behind a
label. Approval and next use were not recorded.

The intended value is less manual evidence gathering and more transparent
decisions, not a claim of measured organization-wide productivity gains or
general performance improvements from a single comparison.

## Innovation

**Agent-driven, bounded self-evolution.** Agents generate instruction candidates
from available findings and, in the opt-in development loop, use evaluation
feedback to inform further revisions. The system preserves the original skill
and each candidate's lineage. Automatic learning from subsequent production
tasks and user feedback remains planned.

**GEPA-inspired improvement, with optional GEPA optimization.** SkillOps
integrates the official [GEPA optimizer](https://github.com/gepa-ai/gepa) as an
opt-in alternative to sequential iteration, using execution feedback and
case-level Pareto parent selection to explore instruction candidates.
This is not a joint quality/cost/time optimizer. Evaluation, independent
confirmation, and human approval remain separate gates.

**Evaluation-set-driven assessment.** The initial instruction-quality rubric
uses criteria derived from Anthropic's skill-authoring guidance. It is a
project-defined assessment, not an official Anthropic benchmark or certification.
Project-scoped task datasets and a separate validation execution path are
implemented. A selectable catalog of alternative instruction-quality guidelines
and organization-specific standards remains planned, with each set's version
and results preserved separately.

**Explicit approval gates.** Automated improvement produces reviewable
candidates, not deployment authority. Separate confirmation and human approval
controls prevent a favorable development evaluation from being treated as
authorization to adopt or execute the candidate. These controls are implemented
capabilities, not a claim that the illustrated candidate completed every stage.

## Technology Foundation

The prototype combines **GitHub Copilot CLI** for agent interactions, **Python
tooling** for evaluation and bounded iteration, **Docker-based project checks**,
**GitHub Actions**, and an **Azure-hosted read-only dashboard**.

Versioned evidence artifacts connect original skills, agent-generated candidates,
evaluation findings, and execution comparisons. Together, these components
support a controlled improvement loop around agent instructions.

**SkillOps proposes. Evidence informs. People approve.**

## Getting Started

From a local checkout on Linux with Python 3.12+, start with inspection commands
that require no authentication or model calls:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip --isolated install --disable-pip-version-check \
  --only-binary=:all: --require-hashes --index-url https://pypi.org/simple \
  -r skillops/requirements-evaluator.txt
python3 skillops/skillops.py --help
python3 skillops/project_results.py catalog
```

Dependency installation accesses the package index. Live evaluation additionally
requires an authenticated Copilot CLI, Docker, and explicitly authorized
invocation/time/Credit limits. Do not enable live evaluation just to view the UI;
use the [offline presentation guide](docs/OPERATIONS.md#offline-presentation-backup).
Private runtime data must not be published by default.

## Documentation and Contributing

| Need | Guide |
| --- | --- |
| Commands, approval gates and historical measurements | [Operating guide](docs/OPERATIONS.md) |
| GEPA installation, scoring and execution limits | [GEPA integration](docs/GEPA.md) |
| Dataset preparation and independent validation | [Project C example](docs/GEPA.md#project-c-dataset-preparation) · [Validation contract](docs/GEPA.md#independent-validation) |
| Project evaluation, result formats and dashboard | [Project evaluation](docs/PROJECT-EVALUATION.md) |
| Replay, iteration, confirmation and approval contracts | [Contracts](docs/HACKATHON-CONTRACTS.md) |
| Publishing reviewed records without evaluation | [Reviewed publication](docs/REVIEWED-PUBLICATION.md) |
| Historical benchmark evidence | [Evidence](evidence/README.md) |
| Planned work and contributions | [Dated roadmap](docs/ROADMAP.md) · [Contributing](CONTRIBUTING.md) |

Propose changes through a feature branch and PR. Preserve evidence and distinguish
what is implemented, what was measured, and what remains a hypothesis or plan.

# Oriexa Skill Registry

This registry maps available Claude Code skills to agent roles. When an agent starts working on a task, it should consult this registry to identify which skills can help accomplish the work.

## How Skills Work

Skills are stored as `.claude/skills/<skill-name>/SKILL.md` files. When Claude Code encounters a task matching a skill's description, it automatically loads the skill's instructions and reference materials.

## Skill Categories

### Document Processing
| Skill | Directory | Use When |
|-------|-----------|----------|
| **PDF** | `pdf/` | Reading, creating, editing, merging, splitting, OCR on PDF files |
| **DOCX** | `docx/` | Creating, editing, analyzing Word documents (.docx) |
| **XLSX** | `xlsx/` | Creating, editing, analyzing Excel spreadsheets |
| **PPTX** | `pptx/` | Creating, editing, analyzing PowerPoint presentations |

### Web Development & Design
| Skill | Directory | Use When |
|-------|-----------|----------|
| **Frontend Design** | `frontend-design/` | Building web components, pages, UIs with high design quality |
| **React Best Practices** | `react-best-practices/` | React/Next.js performance optimization (57 rules) |
| **Composition Patterns** | `composition-patterns/` | React component composition and architecture patterns |
| **React Native** | `react-native-skills/` | React Native mobile app development patterns |
| **D3.js Visualization** | `d3-visualization/` | Creating interactive data visualizations with D3.js |
| **Web Artifacts Builder** | `web-artifacts-builder/` | Building interactive web artifacts |
| **Canvas Design** | `canvas-design/` | HTML Canvas-based designs and animations |
| **Algorithmic Art** | `algorithmic-art/` | Generative/algorithmic art with code |
| **Webapp Testing** | `webapp-testing/` | Testing web apps with Playwright (screenshots, interaction) |

### Deployment & DevOps
| Skill | Directory | Use When |
|-------|-----------|----------|
| **Vercel Deploy** | `vercel-deploy/` | Deploying apps to Vercel (no auth needed, preview + claim URLs) |
| **Senior DevOps** | `senior-devops/` | CI/CD, infrastructure, Docker, Kubernetes, monitoring |
| **AWS Solution Architect** | `aws-solution-architect/` | AWS architecture patterns, service selection, best practices |
| **Incident Commander** | `incident-commander/` | Incident response, post-incident reviews, runbooks |

### Engineering & Code Quality
| Skill | Directory | Use When |
|-------|-----------|----------|
| **Code Reviewer** | `code-reviewer/` | PR analysis, code quality checks, review reports |
| **Senior Architect** | `senior-architect/` | System architecture design, ADRs, dependency analysis |
| **Senior Backend** | `senior-backend/` | Backend development patterns and best practices |
| **Senior Frontend** | `senior-frontend/` | Frontend development patterns and best practices |
| **Senior Fullstack** | `senior-fullstack/` | Full-stack development guidance |
| **Senior QA** | `senior-qa/` | Testing strategies, QA automation, quality assurance |
| **Senior Security** | `senior-security/` | Security best practices, vulnerability assessment |
| **TDD Guide** | `tdd-guide/` | Test-driven development methodology and patterns |
| **Tech Stack Evaluator** | `tech-stack-evaluator/` | Evaluating and choosing technology stacks |

### Data & ML
| Skill | Directory | Use When |
|-------|-----------|----------|
| **Senior Data Engineer** | `senior-data-engineer/` | Data pipelines, ETL, data warehousing |
| **Senior ML Engineer** | `senior-ml-engineer/` | Machine learning model development and deployment |

### Integration & Tools
| Skill | Directory | Use When |
|-------|-----------|----------|
| **MCP Builder** | `mcp-builder/` | Building MCP servers (Model Context Protocol) for tool integration |
| **Skill Creator** | `skill-creator/` | Creating new Claude Code skills |
| **Changelog Generator** | `changelog-generator/` | Generating changelogs from git history |

### Business & Strategy
| Skill | Directory | Use When |
|-------|-----------|----------|
| **CEO Advisor** | `ceo-advisor/` | Executive decision frameworks, board governance |
| **CTO Advisor** | `cto-advisor/` | Technology evaluation, engineering metrics, ADRs |
| **Customer Success Manager** | `customer-success-manager/` | Customer onboarding, health scoring, QBRs |
| **Revenue Operations** | `revenue-operations/` | Pipeline management, forecasting, GTM efficiency |

### Content & Branding
| Skill | Directory | Use When |
|-------|-----------|----------|
| **Brand Guidelines** | `brand-guidelines/` | Creating and maintaining brand identity guidelines |
| **Theme Factory** | `theme-factory/` | Creating cohesive design themes and color palettes |
| **Doc Co-authoring** | `doc-coauthoring/` | Collaborative document editing workflows |
| **Internal Comms** | `internal-comms/` | Company newsletters, FAQ answers, updates |
| **Citation Management** | `citation-management/` | Academic citations, BibTeX, metadata extraction |

## Agent-to-Skill Mapping

### Web Frontend Agent
Primary: `frontend-design`, `react-best-practices`, `composition-patterns`, `webapp-testing`
Secondary: `d3-visualization`, `canvas-design`, `theme-factory`

### API/Backend Agent
Primary: `senior-backend`, `code-reviewer`, `tdd-guide`
Secondary: `senior-architect`, `senior-security`, `mcp-builder`

### Deployment Agent
Primary: `vercel-deploy`, `senior-devops`
Secondary: `aws-solution-architect`, `incident-commander`

### Documentation Agent
Primary: `docx`, `pdf`, `pptx`, `xlsx`
Secondary: `changelog-generator`, `citation-management`, `internal-comms`

### Design Agent
Primary: `frontend-design`, `brand-guidelines`, `theme-factory`
Secondary: `canvas-design`, `algorithmic-art`, `d3-visualization`

### Review/QA Agent
Primary: `code-reviewer`, `senior-qa`, `webapp-testing`
Secondary: `tdd-guide`, `senior-security`

### Data Processing Agent
Primary: `xlsx`, `pdf`, `senior-data-engineer`
Secondary: `senior-ml-engineer`, `d3-visualization`

### Full-Stack Agent
Primary: `senior-fullstack`, `react-best-practices`, `senior-backend`
Secondary: `code-reviewer`, `tdd-guide`, `vercel-deploy`

## Skill Sources

| Source | Repository | Skills Count |
|--------|-----------|-------------|
| Anthropic Official | `anthropics/skills` | 14 skills |
| Vercel Labs | `vercel-labs/agent-skills` | 4 skills (React, Composition, React Native, Deploy) |
| D3.js Skill | `chrisvoncsefalvay/claude-d3js-skill` | 1 skill |
| Alireza Claude Skills | `alirezarezvani/claude-skills` | ~20 skills (engineering, business) |
| K-Dense Scientific | `K-Dense-AI/claude-scientific-skills` | 1 selected (citation-management) |
| Awesome Claude Skills | `ComposioHQ/awesome-claude-skills` | 1 selected (changelog-generator) |

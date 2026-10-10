# Final Project

Choose one of the four options below. Each is a self-contained project that brings together the skills from the whole program — prompting, agents, RAG, evaluation, and production concerns. Requirements and a per-option evaluation checklist are included; the full grading rubric is in `02 - Final Project Rubric.md`.

## Option Overview

| Option | Project | Complexity | Key Skills |
|---|---|---|---|
| A | AI Code Review Bot | Medium | Prompting, API design |
| B | Legacy Code Documenter | Medium-High | Agents, code analysis |
| C | Tech Debt Analyzer | High | RAG, evaluation |
| D | Multi-Agent Research Assistant | High | Multi-agent, orchestration |

## Getting Started

1. Choose your project based on interests and complexity comfort
2. Read the requirements for your selected option
3. Build, test, and deploy within the time limit
4. Prepare your demo following the presentation guidelines

## Time Management

| Phase | Time | Focus |
|---|---|---|
| Setup | 15 min | Scaffold the project and install dependencies |
| Core Features | 1 hr | Implement main functionality |
| Polish | 30 min | Error handling, edge cases |
| Deploy | 15 min | Deploy and verify |
| Demo Prep | 15 min | Prepare presentation |
| Buffer | 15 min | Handle unexpected issues |

**Total: ~2.5 hours**

## Tips for Success

- **Start simple:** Get basic functionality working first
- **Test early:** Don't wait until the end to test
- **Deploy early:** Deploy a working skeleton first, then iterate
- **Manage scope:** Cut features if running behind, not quality
- **Prepare demo:** Decide what to show before you finish coding

---

## Option A: AI Code Review Bot

### Project Overview

Build an API that automatically reviews code and provides structured, actionable feedback.

**Complexity:** Medium · **Estimated Time:** 2-2.5 hours

### Requirements

#### Must Have (Core - 70%)

- [ ] `POST /review` endpoint accepting code + language
- [ ] Returns structured JSON with issues and suggestions
- [ ] Categorizes issues by severity (critical/high/medium/low)
- [ ] Categorizes issues by type (bug/security/performance/style)
- [ ] Handles at least 2 programming languages

#### Should Have (Polish - 20%)

- [ ] Helpful error messages for invalid input
- [ ] Rate limiting
- [ ] Logging
- [ ] Health check endpoint

#### Frontend Requirements

- Web interface to paste or upload code for review
- Language selector and optional focus area checkboxes
- Results panel with: summary, issues list with severity badges, suggestions, metrics
- Responsive design

#### Nice to Have (Bonus - 10%)

- [ ] GitHub webhook integration
- [ ] Inline comment suggestions with line numbers
- [ ] Batch review multiple files

### API Specification

`POST /review`

Request:

```json
{
  "code": "def add(a, b):\n    return a + b",
  "language": "python",
  "focus": ["security", "performance"]
}
```

Response:

```json
{
  "summary": "The code is simple and functional but lacks type hints and documentation.",
  "issues": [
    {
      "severity": "low",
      "category": "style",
      "line": 1,
      "description": "Function lacks type hints",
      "suggestion": "def add(a: int, b: int) -> int:"
    }
  ],
  "suggestions": [
    "Add docstring explaining the function purpose",
    "Consider adding input validation"
  ],
  "metrics": {
    "overall_score": 7,
    "complexity": "low",
    "maintainability": "good"
  }
}
```

### Implementation Steps

1. **Setup (10 min)** - Install dependencies, verify API key
2. **Core Logic (45 min)** - Implement review endpoint, parse LLM response
3. **Multi-language (15 min)** - Add language-specific prompt variations
4. **Error Handling (15 min)** - Input validation, graceful error responses
5. **Deploy (15 min)** - Deploy to Railway, set environment variables
6. **Demo Prep (10 min)** - Prepare sample code to review

### Evaluation Checklist

- [ ] API endpoint works correctly
- [ ] Returns valid structured JSON
- [ ] Issues are properly categorized
- [ ] Handles multiple languages
- [ ] Error handling works
- [ ] Deployed and accessible
- [ ] Demo ready
- [ ] Web frontend for code input and review results display
- [ ] Application deployed to Vercel/Railway/Render (provide URL)

---

## Option B: Legacy Code Documenter

### Project Overview

Build an AI-powered CLI tool that automatically generates comprehensive documentation for undocumented or poorly documented code.

**Complexity:** Medium-High · **Estimated Time:** 2-2.5 hours

### Requirements

#### Must Have (Core - 70%)

- [ ] CLI command accepting file or directory path
- [ ] Parse code to identify functions, classes, and modules
- [ ] Generate inline docstrings for Python (Google/NumPy style)
- [ ] Generate JSDoc comments for JavaScript/TypeScript
- [ ] Generate markdown API documentation file
- [ ] Handle at least 2 programming languages (Python and JavaScript)

#### Should Have (Polish - 20%)

- [ ] Generate project-level README with overview
- [ ] Include usage examples in generated documentation
- [ ] Error handling for unparseable or binary files
- [ ] Progress indicators for batch processing

#### Nice to Have (Bonus - 10%)

- [ ] Generate TypeDoc/Sphinx-ready documentation
- [ ] Detect and preserve existing documentation
- [ ] Interactive mode to review before writing changes
- [ ] Support for custom documentation templates

#### Web App Requirements

- Web interface to upload code files or paste code
- Format selector (inline docstrings, markdown API docs, README)
- Preview panel showing generated documentation
- Download button for generated docs
- Support multiple languages (Python, JavaScript)
- Responsive design

### Implementation Steps

1. **Setup (10 min)** - Install dependencies, test LLM connection
2. **Code Parser - Python (30 min)** - AST-based extraction of functions/classes
3. **Code Parser - JavaScript (20 min)** - Regex-based extraction
4. **Inline Documentation (40 min)** - Docstring generation and insertion
5. **Markdown Generation (30 min)** - API reference with TOC
6. **README Generation (20 min)** - Project overview from code analysis
7. **CLI Polish & Testing (20 min)** - Error handling, progress indicators

### Evaluation Checklist

- [ ] CLI command accepts file/directory path
- [ ] Parses Python code successfully
- [ ] Parses JavaScript code successfully
- [ ] Generates Google-style docstrings for Python
- [ ] Generates JSDoc comments for JavaScript
- [ ] Generates markdown API documentation
- [ ] Generates project README
- [ ] Error handling works correctly
- [ ] Progress indicators show status
- [ ] Tool is ready for demo
- [ ] Web frontend for code upload and documentation preview
- [ ] Application deployed to Vercel/Railway/Render (provide URL)

---

## Option C: Tech Debt Analyzer

### Project Overview

Build a RAG-powered system that indexes codebases, detects technical debt patterns using semantic search, and generates prioritized reports with actionable recommendations.

**Complexity:** Medium-High · **Estimated Time:** 2-2.5 hours

### Requirements

#### Must Have (Core - 70%)

- [ ] Index codebase into vector store (ChromaDB or in-memory)
- [ ] Detect at least 5 technical debt patterns:
  - TODO/FIXME/HACK comments
  - Duplicate code blocks
  - Long functions (>50 lines)
  - High cyclomatic complexity
  - Outdated patterns or imports
- [ ] Score each issue by severity (critical/high/medium/low)
- [ ] Generate JSON report with file paths and line numbers
- [ ] `POST /analyze` API endpoint accepting directory path

#### Should Have (Polish - 20%)

- [ ] Prioritize by impact (frequently changed files = higher priority)
- [ ] Use semantic search to find similar problematic patterns
- [ ] Generate HTML report with code snippets
- [ ] Filter results by tech debt category

#### Frontend Requirements

- Dashboard-style web interface
- Directory/repo input for analysis
- Results dashboard showing: summary stats, issues list filterable by severity/category, recommendations panel
- Visual charts for issue distribution
- Responsive design

#### Nice to Have (Bonus - 10%)

- [ ] Historical tracking (compare with previous scans)
- [ ] GitHub integration (auto-post issues)
- [ ] Customizable tech debt rules via config
- [ ] Refactoring suggestions with code examples

### API Specification

`POST /analyze`

Request:

```json
{
  "directory": "./src",
  "threshold": "medium",
  "categories": ["todos", "complexity", "duplication"]
}
```

Response:

```json
{
  "summary": {
    "total_issues": 47,
    "critical": 3,
    "high": 12,
    "medium": 20,
    "low": 12,
    "scan_time_seconds": 8.5
  },
  "issues": [
    {
      "severity": "high",
      "category": "complexity",
      "file": "src/utils/parser.py",
      "line": 145,
      "description": "Function has cyclomatic complexity of 23 (threshold: 10)",
      "code_snippet": "def parse_complex_structure(data): ...",
      "recommendation": "Refactor into smaller functions with single responsibilities",
      "similar_issues": ["src/handlers/request.py:89"]
    }
  ],
  "recommendations": [
    "Address 3 critical issues in src/utils/ before next release",
    "15 TODO comments suggest incomplete features - consider sprint to resolve"
  ]
}
```

### Implementation Steps

1. **Setup (10 min)** - Install dependencies, set API key
2. **Basic Detectors (40 min)** - TODO, complexity, long function detectors
3. **RAG Indexing (30 min)** - Index codebase with ChromaDB
4. **Similarity Search (25 min)** - Find similar code patterns with RAG
5. **Scoring & Prioritization (20 min)** - Severity calculation and sorting
6. **Report Generation (15 min)** - Complete API endpoint and JSON response
7. **Deploy & Demo (10 min)** - Deploy to Railway, prepare demo

### Evaluation Checklist

- [ ] Indexes codebase into vector store
- [ ] Detects TODO/FIXME comments
- [ ] Detects high complexity functions
- [ ] Detects long functions
- [ ] Scores issues by severity
- [ ] Uses RAG to find similar patterns
- [ ] Generates prioritized recommendations
- [ ] API endpoint works correctly
- [ ] Deployed and accessible
- [ ] Demo ready with real codebase
- [ ] Web dashboard for tech debt analysis results
- [ ] Application deployed to Vercel/Railway/Render (provide URL)

---

## Option D: Multi-Agent Research Assistant

### Project Overview

Build a sophisticated multi-agent system where specialized AI agents (Researcher, Writer, Reviewer) collaborate under a Supervisor to conduct research and produce polished reports.

**Complexity:** High · **Estimated Time:** 2-2.5 hours

### Requirements

#### Must Have (Core - 70%)

- [ ] Three specialized worker agents:
  - **Researcher:** Gathers information and creates summaries
  - **Writer:** Creates polished, structured content
  - **Reviewer:** Reviews content for quality and accuracy
- [ ] Supervisor agent that:
  - Decomposes research questions into subtasks
  - Delegates to appropriate agents
  - Coordinates iterative workflow
  - Synthesizes final output
- [ ] `POST /research` endpoint accepting research question
- [ ] Iterative workflow (research -> write -> review -> refine loop)
- [ ] Returns structured JSON with final report and metadata

#### Should Have (Polish - 20%)

- [ ] Conversation history in API response (shows agent interactions)
- [ ] Configurable parameters (max_iterations, depth level)
- [ ] Error handling and timeout protection
- [ ] Progress tracking in response

#### Frontend Requirements

- Chat-like web interface for submitting research questions
- Depth selector (brief/detailed/technical)
- Real-time progress showing agent workflow (which agent is active)
- Formatted report output with sections
- Conversation history sidebar
- Responsive design

#### Nice to Have (Bonus - 10%)

- [ ] Streaming responses (show real-time progress)
- [ ] Web search tool for Researcher agent
- [ ] Reference tracking and citations
- [ ] Multiple report formats (brief, detailed, technical)
- [ ] Agent performance metrics (tokens used, time taken)

### API Specification

`POST /research`

Request:

```json
{
  "question": "Explain the differences between RAG and fine-tuning for LLMs",
  "depth": "detailed",
  "max_iterations": 5
}
```

Response:

```json
{
  "report": {
    "title": "RAG vs Fine-Tuning for Large Language Models",
    "summary": "RAG and fine-tuning are complementary techniques...",
    "sections": [
      {
        "heading": "Overview",
        "content": "RAG enhances LLM responses by..."
      },
      {
        "heading": "Key Differences",
        "content": "1. RAG provides external knowledge dynamically..."
      }
    ],
    "references": ["Based on analysis of LLM architectures"]
  },
  "metadata": {
    "iterations": 3,
    "agents_used": ["Researcher", "Writer", "Reviewer"],
    "duration_seconds": 45.2
  },
  "conversation_history": [
    {
      "iteration": 0,
      "agent": "Supervisor",
      "action": "DELEGATE",
      "target_agent": "Researcher",
      "task": "Research RAG and fine-tuning techniques..."
    }
  ]
}
```

### Implementation Steps

1. **Setup (10 min)** - Install dependencies, set API key
2. **Worker Agents (30 min)** - Implement Researcher, Writer, Reviewer with prompts
3. **Supervisor Logic (40 min)** - Delegation parsing, context building, coordination
4. **Response Formatting (30 min)** - Markdown parsing into structured report
5. **Workflow Integration (20 min)** - Connect to API, test full workflow
6. **Iteration Handling (15 min)** - Max iterations, force completion, edge cases
7. **Deploy & Demo (15 min)** - Deploy to Railway, prepare demo questions

### Evaluation Checklist

- [ ] Three specialized agents implemented
- [ ] Supervisor coordinates agents correctly
- [ ] Delegates tasks with DELEGATE/TASK format
- [ ] Builds context from previous results
- [ ] Parses markdown into structured report
- [ ] API endpoint returns correct JSON format
- [ ] Conversation history captured
- [ ] Handles max iterations limit
- [ ] Error handling works
- [ ] Deployed and accessible
- [ ] Demo ready with sample questions
- [ ] Web frontend with chat interface and research workflow visualization
- [ ] Application deployed to Vercel/Railway/Render (provide URL)

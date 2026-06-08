# Gmail Clone

A faithful Gmail web interface mockup using React + Tailwind frontend and FastAPI + SQLite backend.

## Quick Start

### 1. Install dependencies

```bash
cd /home/hmozannar/repos/agento_env/envs/gmail

# Backend (using uv)
uv sync --all-extras

# Frontend
cd frontend
npm install
cd ..
```

### 2. Seed the database

```bash
uv run python -m backend.seed
```

See the [Data Generation](#data-generation) section for more seeding options.
### 3. Run the app

**Option A: Production mode (single server)**
```bash
cd frontend && npm run build && cd ..
uv run python -m backend.app --port 8023 --host 0.0.0.0
```
Open http://localhost:8000

**Option B: Development mode (hot-reload)**
```bash
# Terminal 1: Backend
uv run python -m backend.app --port 8000 --reload

# Terminal 2: Frontend dev server
cd frontend
npm run dev
```
Open http://localhost:5173 (frontend proxies API to :8000)

## Features

- Full email list with read/unread styling
- Star, archive, delete, mark spam actions
- Bulk operations on selected emails
- Compose, reply, forward emails
- Folder navigation (Inbox, Sent, Drafts, Starred, Spam, Trash, etc.)
- Label management with colors
- Search functionality
- Settings pages (General, Labels, Inbox, Filters, Signature, Vacation, Themes)
- Dark/light theme support
- Responsive sidebar

## Testing

```bash
# Run all tests (92 tests)
uv run pytest

# Run with verbose output
uv run pytest -v

# Run specific test file
uv run pytest tests/test_emails.py
```

## API Endpoints

### Emails
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/emails` | List emails (supports folder, category, label_id, is_unread, q filters) |
| GET | `/api/emails/:id` | Get single email |
| POST | `/api/emails` | Send/create email |
| PUT | `/api/emails/:id` | Update email flags |
| DELETE | `/api/emails/:id` | Move to trash |
| POST | `/api/emails/:id/archive` | Archive email |
| POST | `/api/emails/:id/star` | Toggle star |
| POST | `/api/emails/:id/read` | Mark as read |
| POST | `/api/emails/:id/spam` | Report as spam |
| POST | `/api/emails/:id/snooze` | Snooze email |

### Bulk Actions
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/emails/bulk/archive` | Archive multiple |
| POST | `/api/emails/bulk/delete` | Delete multiple |
| POST | `/api/emails/bulk/read` | Mark multiple as read |
| POST | `/api/emails/bulk/labels` | Add labels to multiple |

### Labels
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/labels` | List all labels |
| POST | `/api/labels` | Create label |
| PUT | `/api/labels/:id` | Update label |
| DELETE | `/api/labels/:id` | Delete label |

### Other
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/user` | Get current user |
| GET | `/api/stats` | Get mailbox stats |
| GET | `/api/settings` | Get user settings |
| PUT | `/api/settings` | Update settings |
| GET | `/api/search?q=` | Search emails |
| GET | `/api/contacts/autocomplete?q=` | Contact autocomplete |
| GET | `/api/drafts` | List drafts |
| GET | `/api/scheduled` | List scheduled emails |
| GET | `/api/snoozed` | List snoozed emails |
| GET | `/api/filters` | List email filters |
| GET | `/api/blocked` | List blocked senders |

Full API documentation: http://localhost:8000/docs

## Project Structure

```
gmail/
├── pyproject.toml          # Python dependencies
├── backend/
│   ├── app.py              # FastAPI app setup
│   ├── routes.py           # All API endpoints (50+)
│   ├── models.py           # 13 SQLModel data models
│   ├── database.py         # SQLite configuration
│   └── seed.py             # Database seeding
├── frontend/
│   ├── src/
│   │   ├── types/index.ts  # TypeScript interfaces
│   │   ├── api.ts          # API client
│   │   ├── utils/index.ts  # Utility functions
│   │   ├── App.tsx         # Main app with routing
│   │   └── components/
│   │       ├── Header.tsx
│   │       ├── Sidebar.tsx
│   │       ├── EmailList.tsx
│   │       ├── EmailRow.tsx
│   │       ├── EmailDetail.tsx
│   │       ├── ComposeModal.tsx
│   │       ├── Settings.tsx
│   │       └── Toast.tsx
│   ├── index.html
│   └── package.json
├── tests/                  # 92 pytest tests
│   ├── test_emails.py
│   ├── test_threads.py
│   ├── test_labels.py
│   ├── test_drafts.py
│   ├── test_filters.py
│   ├── test_scheduled.py
│   ├── test_search.py
│   └── test_settings.py
├── GMAIL_SPEC.md           # Full specification
└── README.md
```

## Data Generation

The app supports multiple database seeding strategies for different use cases.

### Option 1: Basic Seed (Single User)

Simple seed with one user and sample emails:

```bash
uv run python -m backend.seed
```

Creates:
- 1 user (John Doe, me@gmail.com)
- 15+ contacts
- 50+ sample emails across folders
- System and user labels
- Filters, blocked senders, scheduled/snoozed emails

### Option 2: Company Seed (Template-Based)

Generate a realistic 20-person company with template-based emails:

```bash
uv run python -m backend.seed_company --db ./gmail_company.db
```

### Option 3: Company Seed with LLM (Recommended)

Generate highly realistic emails using an LLM for natural language:

```bash
uv run python -m backend.seed_company_llm \
  --db ./gmail_company_llm.db \
  --endpoint /path/to/endpoint_configs \
  --emails-per-user 15 \
  --eval-model gpt-5
```

**Options:**
| Flag | Default | Description |
|------|---------|-------------|
| `--db` | `./gmail_company_llm.db` | Output database path |
| `--endpoint` | `.../endpoint_configs_gpt5/dev` | LLM endpoint config directory |
| `--emails-per-user` | `15` | Emails to generate per user |
| `--eval-model` | `gpt-5` | Model filter: `gpt-4o`, `gpt-5`, `o3-mini`, `*` |

### Running with Custom Database

```bash
# Generate data
uv run python -m backend.seed_company_llm --db ./my_data.db

# Run app with that database
uv run python -m backend.app --port 8023 --db ./my_data.db
```

## Admin API

Endpoints for database management and evaluation.

### Reset Database

Reset the running database from a source file:

```bash
curl -X POST http://localhost:8023/api/admin/reset \
  -H "Content-Type: application/json" \
  -d '{"source_db": "gmail_company.db"}'
```

### Download Database

Download the current database file:

```bash
curl http://localhost:8023/api/admin/db -o backup.db
```

### Evaluation Workflow

```bash
# 1. Start server with working database
uv run python -m backend.app --port 8023 --db ./gmail.db

# 2. Reset to clean state
curl -X POST http://localhost:8023/api/admin/reset \
  -H "Content-Type: application/json" \
  -d '{"source_db": "./gmail_company.db"}'

# 3. Perform actions (via UI or API)

# 4. Check results
curl http://localhost:8023/api/admin/eval

# 5. Download final state (optional)
curl http://localhost:8023/api/admin/db -o result.db

# 6. Reset for next run
curl -X POST http://localhost:8023/api/admin/reset \
  -H "Content-Type: application/json" \
  -d '{"source_db": "./gmail_company.db"}'
```